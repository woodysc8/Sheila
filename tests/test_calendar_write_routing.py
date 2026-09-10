import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import calendar_store
import personal_calendar
from agents.router import RoutingDecision
from agents.workflow import handle_request


EASTERN = ZoneInfo("America/New_York")
THURSDAY = datetime(2026, 9, 10, 12, tzinfo=EASTERN)


class _ThursdayClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return THURSDAY.astimezone(tz) if tz else THURSDAY.replace(tzinfo=None)


class CalendarWriteRoutingTests(unittest.TestCase):
    """Exercise Sheila's real request entry point with only fake local writes."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(calendar_store.config, "DB_PATH", os.path.join(self.temp_dir.name, "calendar.db"))
        self.db_patch.start()
        self.clock_patch = patch.object(personal_calendar, "datetime", _ThursdayClock)
        self.clock_patch.start()

    def tearDown(self):
        self.clock_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _successful_event(self, title, start, end, **_kwargs):
        return {"id": 1, "title": title, "start": start.isoformat(), "end": end.isoformat()}

    def test_ordinal_commitment_routes_to_writer_without_llm(self):
        llm = Mock(return_value="generic fallback")
        with patch.object(personal_calendar, "create_personal_calendar_event", side_effect=self._successful_event) as create:
            result = handle_request("October 2nd I am going to Zach Bryan at Gillette Stadium", llm)
        self.assertEqual(result["route"]["capability"], "personal_calendar")
        create.assert_called_once()
        self.assertEqual(create.call_args.args[0], "Zach Bryan at Gillette Stadium")
        self.assertIn("Added 1 personal-calendar event", result["response"])
        llm.assert_not_called()

    def test_date_range_routes_to_writer_without_llm(self):
        llm = Mock(return_value="generic fallback")
        with patch.object(personal_calendar, "create_personal_calendar_event", side_effect=self._successful_event) as create:
            result = handle_request("November 18th to the 22nd I am taking PTO and going to the DR with Nora", llm)
        self.assertEqual(result["route"]["capability"], "personal_calendar")
        create.assert_called_once()
        start, end = create.call_args.args[1:3]
        self.assertEqual(start.date().isoformat(), "2026-11-18")
        self.assertEqual(end.date().isoformat(), "2026-11-23")
        self.assertIn("Added 1 personal-calendar event", result["response"])
        llm.assert_not_called()

    def test_relative_commitment_uses_eastern_test_clock_end_to_end(self):
        llm = Mock(return_value="generic fallback")
        with patch.object(personal_calendar, "create_personal_calendar_event", side_effect=self._successful_event) as create:
            result = handle_request("Nora is coming this Friday and leaving Saturday before dinner", llm)
        self.assertEqual(result["route"]["capability"], "personal_calendar")
        create.assert_called_once()
        start, end = create.call_args.args[1:3]
        self.assertEqual(start.date().isoformat(), "2026-09-11")
        self.assertEqual(end.date().isoformat(), "2026-09-13")  # exclusive all-day end; Saturday is included
        self.assertIn("Added 1 personal-calendar event", result["response"])
        llm.assert_not_called()

    def test_question_does_not_write_an_event(self):
        llm = Mock(return_value="I can look that up.")
        with patch.object(personal_calendar, "create_personal_calendar_event") as create:
            result = handle_request("When am I going to Zach Bryan?", llm)
        create.assert_not_called()
        self.assertNotEqual(result["route"].get("capability"), "personal_calendar")
        llm.assert_called_once()

    def test_forced_generic_fallback_cannot_claim_unverified_calendar_write(self):
        llm = Mock(return_value="I've added the event to your calendar.")
        generic = RoutingDecision("Sheila", "general_coordination", False, "forced generic route")
        with patch("agents.workflow.route_request", return_value=generic), \
             patch.object(personal_calendar, "create_personal_calendar_event") as create:
            result = handle_request("October 2nd I am going to Zach Bryan at Gillette Stadium", llm)
        create.assert_not_called()
        self.assertEqual(result["response"], "I couldn't confirm that calendar event was added.")
        llm.assert_called_once()


if __name__ == "__main__":
    unittest.main()
