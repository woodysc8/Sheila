import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import sheila_tasks
import personal_calendar
from agents.router import route_request
from agents.workflow import handle_request


EASTERN = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 9, 12, tzinfo=EASTERN)


class SheilaTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(sheila_tasks.config, "DB_PATH", os.path.join(self.temp_dir.name, "memory.db"))
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_create_list_update_cancel_complete_and_date_only(self):
        created = sheila_tasks.handle_request("Remind me to call Mom tomorrow at 5.", NOW)
        self.assertIn("Reminder set", created)
        task = sheila_tasks.list_tasks()[0]
        self.assertEqual(task["text"], "call Mom")
        self.assertIn("T17:00:00-04:00", task["due_at"])

        sheila_tasks.handle_request("Change my reminder to call Mom to Friday at 6.", NOW)
        updated = sheila_tasks.list_tasks()[0]
        self.assertIn("2026-09-11T18:00:00-04:00", updated["due_at"])

        sheila_tasks.handle_request("Remind me to bring my passport Thursday.", NOW)
        date_only = next(task for task in sheila_tasks.list_tasks() if task["text"] == "bring my passport")
        self.assertEqual(date_only["due_date"], "2026-09-10")
        self.assertIsNone(date_only["due_at"])

        sheila_tasks.handle_request("Mark my reminder call Mom done.", NOW)
        completed = sheila_tasks.list_tasks(status="completed")
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["text"], "call Mom")
        sheila_tasks.handle_request("Cancel my reminder to bring my passport.", NOW)
        self.assertEqual(sheila_tasks.list_tasks(), [])

    def test_tentative_reminder_does_not_mutate(self):
        response = sheila_tasks.handle_request("Maybe remind me to call Mom tomorrow.", NOW)
        self.assertIn("didn't create", response)
        self.assertEqual(sheila_tasks.list_tasks(), [])
        self.assertEqual(route_request("Maybe remind me to call Mom tomorrow.").capability, None)

    def test_combined_calendar_lookup_separates_personal_and_timed_work(self):
        brain = Mock()
        work_events = [
            {"title": "Standup", "start": "2026-09-10T09:00:00-04:00", "end": "2026-09-10T09:30:00-04:00", "location": ""},
            {"title": "Company holiday", "start": "2026-09-10", "end": "2026-09-11", "location": ""},
        ]
        with patch("agents.workflow.calendar.get_events", return_value=work_events):
            result = handle_request("What do I have tomorrow?", brain)
        self.assertNotIn("[WORK CALENDAR RESULTS]", result["response"])
        self.assertIn("Standup", result["response"])
        self.assertNotIn("Company holiday", result["response"])
        self.assertNotIn("[PERSONAL CALENDAR RESULTS]", result["response"])
        self.assertNotIn("2026-09-10T09:00:00", result["response"])
        brain.assert_not_called()

    def test_calendar_lookup_uses_conversational_times(self):
        brain = Mock()
        work_events = [{"title": "All Hands Call", "start": "2026-09-11T11:30:00-04:00", "end": "2026-09-11T12:00:00-04:00", "location": ""}]
        with patch("agents.workflow.calendar.get_events", return_value=work_events):
            result = handle_request("What are my work meetings Friday?", brain)
        self.assertEqual(result["response"], "You have All Hands Call at 11:30 AM.")
        self.assertNotIn("America/New_York", result["response"])

    def test_personal_and_work_lookup_is_conversational(self):
        personal_calendar.handle_personal_calendar_request("Nora is coming Friday at 7.", NOW)
        brain = Mock()
        work_events = [{"title": "All Hands Call", "start": "2026-09-11T11:30:00-04:00", "end": "2026-09-11T12:00:00-04:00", "location": ""}]
        with patch("agents.workflow.calendar.get_events", return_value=work_events):
            result = handle_request("What do I have Friday?", brain)
        self.assertIn("Friday you have All Hands Call at 11:30 AM and Nora is coming at 7 PM.", result["response"])
        self.assertNotIn("[PERSONAL CALENDAR RESULTS]", result["response"])
        self.assertNotIn("[WORK CALENDAR RESULTS]", result["response"])
        self.assertNotIn("2026-09-11T", result["response"])


if __name__ == "__main__":
    unittest.main()