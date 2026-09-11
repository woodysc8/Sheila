import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import calendar_store
import config
import operational_store
import reminder_worker
import sheila_tasks
from agents.router import route_request
from migrate_operational_state import migrate


ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 10, 12, tzinfo=ET)


class OperationalReminderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.url = "sqlite:///" + os.path.join(self.tmp.name, "operational.db")
        self.patch = patch.object(operational_store.config, "SHEILA_OPERATIONAL_DATABASE_URL", self.url); self.patch.start()

    def tearDown(self): self.patch.stop(); self.tmp.cleanup()

    def test_calendar_and_reminder_survive_reopen(self):
        event = calendar_store.create_event("Durable", NOW, NOW + timedelta(hours=1))
        reminder = operational_store.create_reminder("Text Bo", NOW + timedelta(minutes=1), "America/New_York")
        self.assertEqual(calendar_store.get_event(event["id"])["title"], "Durable")
        self.assertEqual(operational_store.list_reminders()[0]["id"], reminder["id"])

    def test_tomorrow_without_time_uses_nine_am(self):
        response = sheila_tasks.handle_request("Remind me to text Bo tmw", NOW)
        self.assertIn("tomorrow at 9:00 AM", response)
        self.assertIn("2026-09-11T09:00:00", operational_store.list_reminders()[0]["due_at"])

    def test_undated_reminder_two_hour_default_time_boundaries(self):
        cases = (
            ((10, 0), "2026-09-10T12:00:00", "today at 12:00 PM"),
            ((16, 0), "2026-09-10T18:00:00", "today at 6:00 PM"),
            ((18, 0), "2026-09-10T20:00:00", "today at 8:00 PM"),
            ((19, 0), "2026-09-11T13:00:00", "tomorrow at 1:00 PM"),
            ((20, 29), "2026-09-11T13:00:00", "tomorrow at 1:00 PM"),
            ((20, 30), "2026-09-11T13:00:00", "tomorrow at 1:00 PM"),
            ((23, 0), "2026-09-11T13:00:00", "tomorrow at 1:00 PM"),
            ((3, 0), "2026-09-10T13:00:00", "today at 1:00 PM"),
            ((5, 59), "2026-09-10T13:00:00", "today at 1:00 PM"),
            ((6, 0), "2026-09-10T08:00:00", "today at 8:00 AM"),
        )
        for (hour, minute), expected_due, expected_confirmation in cases:
            with self.subTest(hour=hour, minute=minute):
                current = datetime(2026, 9, 10, hour, minute, tzinfo=ET)
                before_count = len(operational_store.list_reminders())
                response = sheila_tasks.handle_request("Remind me to get shampoo", current)
                reminders = operational_store.list_reminders()
                self.assertEqual(len(reminders), before_count + 1)
                self.assertTrue(any(
                    reminder["text"] == "get shampoo" and reminder["due_at"].startswith(expected_due)
                    for reminder in reminders
                ))
                self.assertIn(expected_confirmation, response)

    def test_explicit_time_without_date_uses_next_local_occurrence(self):
        self.assertIn("today at 5:00 PM", sheila_tasks.handle_request("Remind me to get shampoo at 5 PM", NOW))
        self.assertTrue(operational_store.list_reminders()[0]["due_at"].startswith("2026-09-10T17:00:00"))
        after_five = datetime(2026, 9, 10, 17, 1, tzinfo=ET)
        self.assertIn("tomorrow at 5:00 PM", sheila_tasks.handle_request("Remind me to call Mom at 5 PM", after_five))
        self.assertTrue(operational_store.list_reminders()[1]["due_at"].startswith("2026-09-11T17:00:00"))

    def test_default_confirmation_requires_successful_persistence(self):
        with patch.object(operational_store, "create_reminder", side_effect=RuntimeError("database unavailable")):
            with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                sheila_tasks.handle_request("Remind me to get shampoo", NOW)
        self.assertEqual(operational_store.list_reminders(), [])

    def test_relative_minutes_are_routed_and_persisted_in_both_word_orders(self):
        for text in (
            "Remind me in 2 minutes to text Bo",
            "Remind me to text Bo in 2 minutes",
        ):
            self.assertEqual(route_request(text).capability, "sheila_task")
            response = sheila_tasks.handle_request(text, NOW)
            self.assertIn("Reminder set", response)
        reminders = operational_store.list_reminders()
        self.assertEqual([item["text"] for item in reminders], ["text Bo", "text Bo"])
        self.assertTrue(all(item["due_at"].startswith("2026-09-10T12:02:00") for item in reminders))

    def test_relative_hours_are_persisted_with_exact_due_time(self):
        response = sheila_tasks.handle_request("Remind me in 2 hours to call Mom", NOW)
        self.assertIn("Reminder set: call Mom", response)
        reminder = operational_store.list_reminders()[0]
        self.assertEqual(reminder["text"], "call Mom")
        self.assertTrue(reminder["due_at"].startswith("2026-09-10T14:00:00"))

    def test_relative_an_hour_and_seconds_resolve_without_llm(self):
        self.assertEqual(sheila_tasks._parse_due("Remind me in an hour to stretch", NOW)[1], "2026-09-10T13:00:00-04:00")
        self.assertEqual(sheila_tasks._parse_due("Remind me in 30 seconds to test", NOW)[1], "2026-09-10T12:00:30-04:00")

    def test_configured_user_id_owns_default_reminder(self):
        with patch.object(config, "SHEILA_USER_ID", "sam-production"):
            sheila_tasks.handle_request("Remind me to text Bo tomorrow", NOW)
            self.assertEqual(operational_store.list_reminders("sam-production")[0]["text"], "text Bo")

    def test_monthly_multiple_dates_and_next_delivery(self):
        response = sheila_tasks.handle_request("Remind me about rent on the 22nd and 25th every month", NOW)
        self.assertIn("22", response); self.assertEqual(len(operational_store.list_reminders()), 2)

    def test_monthly_days_29_30_31_are_not_changed(self):
        january = datetime(2026, 2, 1, 12, tzinfo=ET)
        for day, expected in ((29, "2026-03-29"), (30, "2026-03-30"), (31, "2026-03-31")):
            sheila_tasks.handle_request(f"Remind me about rent on the {day}th every month", january)
            item = operational_store.list_reminders()[-1]
            self.assertTrue(item["due_at"].startswith(expected))
            self.assertIn(f'"day": {day}', item["recurrence"])

    def test_text_and_existing_commands_are_preserved_in_operational_mode(self):
        response = sheila_tasks.handle_request("Remind me to call Mom about next year's plan tomorrow at 5 PM", NOW)
        self.assertIn("call Mom about next year's plan", response)
        self.assertIn("didn't create", sheila_tasks.handle_request("Maybe remind me to call Dad tomorrow at 5", NOW))
        self.assertIn("Updated", sheila_tasks.handle_request("Change my reminder to call Mom about next year's plan to Friday at 6", NOW))
        self.assertIn("Completed", sheila_tasks.handle_request("Mark my reminder call Mom about next year's plan done", NOW))
        sheila_tasks.handle_request("Remind me to buy milk tomorrow at 5", NOW)
        self.assertIn("Cancelled", sheila_tasks.handle_request("Cancel my reminder to buy milk", NOW))

    def test_production_mode_requires_operational_url(self):
        with patch.object(config, "SHEILA_OPERATIONAL_DATABASE_URL", ""), patch.object(config, "SHEILA_REQUIRE_OPERATIONAL_DATABASE", True):
            with self.assertRaises(operational_store.OperationalStoreError):
                sheila_tasks.handle_request("Remind me to call Mom tomorrow at 5", NOW)

    def test_delivery_success_failure_retry_and_no_duplicate(self):
        operational_store.create_reminder("Text Bo", NOW - timedelta(minutes=1), "America/New_York")
        sent = []
        self.assertTrue(reminder_worker.dispatch_once(NOW, lambda item: sent.append(item["id"])))
        self.assertEqual(len(sent), 1); self.assertFalse(reminder_worker.dispatch_once(NOW, lambda _item: None))
        operational_store.create_reminder("Retry", NOW - timedelta(minutes=1), "America/New_York")
        self.assertFalse(reminder_worker.dispatch_once(NOW, lambda _item: (_ for _ in ()).throw(RuntimeError("down"))))
        self.assertTrue(reminder_worker.dispatch_once(NOW, lambda _item: None))

    def test_migration_is_dry_run_and_idempotent(self):
        source = os.path.join(self.tmp.name, "source.db"); conn = __import__("sqlite3").connect(source)
        conn.execute("CREATE TABLE calendar_events (id INTEGER,title TEXT,description TEXT,start_at TEXT,end_at TEXT,timezone TEXT,location TEXT)")
        conn.execute("INSERT INTO calendar_events VALUES (1,'Old','',?,?, 'America/New_York','')", (NOW.isoformat(), (NOW+timedelta(hours=1)).isoformat())); conn.commit(); conn.close()
        self.assertEqual(migrate(source)["migrated"], 0)
        self.assertEqual(migrate(source, True)["migrated"], 1)
        self.assertEqual(migrate(source, True)["skipped"], 1)


if __name__ == "__main__": unittest.main()
