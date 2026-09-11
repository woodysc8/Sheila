import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import calendar_store
import personal_calendar
from agents.router import route_request


NOW = datetime(2026, 9, 11, 12, tzinfo=ZoneInfo("America/New_York"))
REQUEST = """November 18th to the 22nd I am taking PTO and going to the DR with Nora
October 2nd I am going to Zach Bryan at Gillette Stadium
October 21st to 23rd I am taking the Amtrak to Philly for my company retreat- alumni weekend is that weekend and I will be going back to HC for that Saturday and Sunday.
Nora is coming up this Friday and leaving Saturday before dinner- I have a doctors appointment for a new primary Saturday in the morning"""


class ConversationalCalendarIntentTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(calendar_store.config, "DB_PATH", os.path.join(self.temp_dir.name, "calendar.db"))
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_combined_memory_calendar_request_routes_to_calendar(self):
        self.assertEqual(route_request("save this to memory and add it to my calendar October 2nd I am going to Zach Bryan").capability, "personal_calendar")

    def test_batch_extracts_all_day_ranges_and_unknown_time_without_fake_time(self):
        events = personal_calendar.extract_definite_event_candidates(REQUEST, NOW)
        by_title = {event.title: event for event in events}
        self.assertEqual(len(events), 6)
        self.assertEqual(by_title["Holy Cross Alumni Weekend"].start.date().isoformat(), "2026-10-24")
        self.assertEqual(by_title["Holy Cross Alumni Weekend"].end.date().isoformat(), "2026-10-26")
        doctor = next(event for event in events if "doctors appointment" in event.title.lower())
        self.assertTrue(doctor.all_day)
        self.assertEqual(doctor.start.date().isoformat(), "2026-09-12")

    def test_batch_execution_is_idempotent(self):
        first = personal_calendar._create_definite_event_candidates(REQUEST, NOW)
        second = personal_calendar._create_definite_event_candidates(REQUEST, NOW)
        self.assertIn("Added 6 personal-calendar events", first)
        self.assertIn("Already on your personal Google Calendar", second)
        events = calendar_store.list_events("2026-09-01T00:00:00", "2026-12-01T00:00:00", "America/New_York")
        self.assertEqual(len(events), 6)


if __name__ == "__main__":
    unittest.main()
