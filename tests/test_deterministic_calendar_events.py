import os
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import calendar_store
import personal_calendar
from agents.router import route_request
from agents.workflow import _calendar_range, handle_request


EASTERN = ZoneInfo("America/New_York")
THURSDAY = datetime(2026, 9, 10, 12, tzinfo=EASTERN)
FRIDAY = datetime(2026, 9, 11, 12, tzinfo=EASTERN)


class DeterministicCalendarEventTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(calendar_store.config, "DB_PATH", os.path.join(self.temp_dir.name, "calendar.db"))
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_ordinal_date_is_a_confirmed_all_day_write(self):
        text = "October 2nd I am going to Zach Bryan at Gillette Stadium"
        self.assertEqual(route_request(text).capability, "personal_calendar")
        response = personal_calendar.handle_personal_calendar_request(text, THURSDAY)
        self.assertIn("Added 1 personal-calendar event", response)
        event = calendar_store.list_events("2026-10-02T00:00:00", "2026-10-03T00:00:00", "America/New_York")[0]
        self.assertEqual(event["title"], "Zach Bryan at Gillette Stadium")
        self.assertTrue(event["start"].startswith("2026-10-02T00:00:00"))
        self.assertTrue(event["end"].startswith("2026-10-03T00:00:00"))

    def test_ranges_and_multiline_commitments_create_every_candidate(self):
        text = """November 18th to the 22nd I am taking PTO and going to the DR with Nora
October 2nd I am going to Zach Bryan at Gillette Stadium
October 21st to 23rd I am taking the Amtrak to Philly for my company retreat
Nora is coming this Friday and leaving Saturday before dinner"""
        candidates = personal_calendar.extract_definite_event_candidates(text, THURSDAY)
        self.assertEqual(len(candidates), 4)
        self.assertTrue(candidates[0].all_day)
        self.assertEqual(candidates[0].start.date().isoformat(), "2026-11-18")
        self.assertEqual(candidates[0].end.date().isoformat(), "2026-11-23")
        self.assertEqual(candidates[-1].start.date().isoformat(), "2026-09-11")
        self.assertEqual(candidates[-1].end.date().isoformat(), "2026-09-13")
        response = personal_calendar.handle_personal_calendar_request(text, THURSDAY)
        self.assertIn("Added 4 personal-calendar events", response)
        self.assertEqual(len(calendar_store.list_events("2026-09-01T00:00:00", "2026-12-01T00:00:00", "America/New_York")), 4)

    def test_questions_and_tentative_language_never_create_events(self):
        for text in ("When am I going to Zach Bryan?", "Maybe I am going to Zach Bryan October 2nd"):
            self.assertEqual(personal_calendar.extract_definite_event_candidates(text, THURSDAY), [])
        self.assertEqual(calendar_store.list_events("2026-09-01T00:00:00", "2026-11-01T00:00:00", "America/New_York"), [])

    def test_relative_date_policy_is_shared_by_writes_and_reads(self):
        self.assertEqual(personal_calendar.resolve_calendar_date("this Friday", THURSDAY).isoformat(), "2026-09-11")
        self.assertEqual(personal_calendar.resolve_calendar_date("Friday", THURSDAY).isoformat(), "2026-09-11")
        self.assertEqual(personal_calendar.resolve_calendar_date("next Friday", THURSDAY).isoformat(), "2026-09-18")
        self.assertEqual(personal_calendar.resolve_calendar_date("this Friday", FRIDAY).isoformat(), "2026-09-11")
        self.assertEqual(personal_calendar.resolve_calendar_date("next Friday", FRIDAY).isoformat(), "2026-09-18")
        self.assertEqual(_calendar_range("What is on my calendar next Friday?", FRIDAY)[0].date().isoformat(), "2026-09-18")

    def test_write_failure_is_reported_and_not_claimed_as_success(self):
        with patch.object(personal_calendar, "create_personal_calendar_event", side_effect=calendar_store.CalendarError("disk unavailable")):
            response = personal_calendar.handle_personal_calendar_request("October 2nd I am going to Zach Bryan", THURSDAY)
        self.assertIn("couldn't add", response)
        self.assertNotIn("Added", response)
        self.assertEqual(calendar_store.list_events("2026-10-02T00:00:00", "2026-10-03T00:00:00", "America/New_York"), [])

    def test_workflow_uses_deterministic_writer_not_the_llm(self):
        responder = unittest.mock.Mock(return_value="I am adding it")
        response = handle_request("October 2nd I am going to Zach Bryan", responder)["response"]
        self.assertIn("Added 1 personal-calendar event", response)
        responder.assert_not_called()


if __name__ == "__main__":
    unittest.main()
