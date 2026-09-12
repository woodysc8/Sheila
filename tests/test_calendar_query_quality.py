import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from agents.router import route_request
import personal_calendar
import sheila_handler
import sheila_tasks


NOW = datetime(2026, 9, 12, 12, tzinfo=ZoneInfo("America/New_York"))


class CalendarQueryQualityTests(unittest.TestCase):
    def test_next_two_month_weekends_filters_deduplicates_sorts_and_groups(self):
        events = [
            {"id": "weekday", "title": "Workday", "start": "2026-09-14T09:00:00-04:00", "end": "2026-09-14T10:00:00-04:00", "all_day": False},
            {"id": "sun", "title": "Sunday brunch", "start": "2026-09-20T11:00:00-04:00", "end": "2026-09-20T12:00:00-04:00", "all_day": False},
            {"id": "sat", "title": "Saturday hike", "start": "2026-09-19T09:00:00-04:00", "end": "2026-09-19T10:00:00-04:00", "all_day": False},
            {"id": "sat", "title": "Saturday hike", "start": "2026-09-19T09:00:00-04:00", "end": "2026-09-19T10:00:00-04:00", "all_day": False},
            {"id": "trip", "title": "Weekend trip", "start": "2026-09-18", "end": "2026-09-21", "all_day": True},
        ]
        with patch.object(personal_calendar, "get_personal_calendar_events", return_value=events) as read:
            reply = personal_calendar.handle_personal_calendar_request("What do I have coming up over the next two months on the weekends?", NOW)
        self.assertIn("Saturday, September 19", reply)
        self.assertIn("Sunday, September 20", reply)
        self.assertNotIn("Workday", reply)
        self.assertEqual(reply.count("Saturday hike"), 1)
        self.assertEqual(reply.count("Weekend trip"), 2)
        self.assertLess(reply.index("Saturday hike"), reply.index("Sunday brunch"))
        self.assertEqual(read.call_count, 1)

    def test_explicit_create_normalizes_title_date_location_and_lookup_reads_google_result(self):
        created = {"id": "google-zach", "title": "Concert Zach Bryan", "start": "2026-10-02", "end": "2026-10-03", "all_day": True, "location": "Gillette Stadium"}
        with patch.object(personal_calendar, "_find_events", side_effect=[[], [created]]), \
             patch.object(personal_calendar, "create_personal_calendar_event", return_value=created) as create:
            reply = personal_calendar.handle_personal_calendar_request("Concert Zach Bryan October 2nd at Gillette stadium add to my calendar", NOW)
            lookup = personal_calendar.handle_personal_calendar_request("When am I going to Zach Bryan?", NOW)
        self.assertIn("Added Concert Zach Bryan", reply)
        self.assertIn("Concert Zach Bryan", lookup)
        self.assertEqual(create.call_args.args[0], "Concert Zach Bryan")
        self.assertEqual(create.call_args.kwargs["location"], "Gillette stadium")
        self.assertNotIn("add to my calendar", create.call_args.args[0].lower())

    def test_existing_travel_question_combines_calendar_and_sam2_without_travel_routing(self):
        question = "And when am I going to the Dominican Republic?"
        self.assertEqual(route_request(question).capability, "personal_calendar")
        with patch.object(sheila_handler.personal_calendar, "handle_personal_calendar_request", return_value="No personal-calendar events found."), \
             patch.object(sheila_handler.memory, "recall", return_value=[{"content": "Sam is taking PTO and traveling to the Dominican Republic with Nora November 18–22, 2026."}]), \
             patch.object(sheila_handler.memory, "log_exchange"):
            reply = sheila_handler.process_message(question)
        self.assertIn("Google Calendar", reply)
        self.assertIn("Sam 2", reply)
        self.assertIn("Dominican Republic", reply)

    def test_existing_flight_question_is_not_travel_but_search_is(self):
        self.assertEqual(route_request("Do I have a flight on November 18th to the Dominican Republic?").capability, "personal_calendar")
        self.assertEqual(route_request("Find flights to the Dominican Republic on November 18th.").agent, "Travel")

    def test_reminder_prefix_stays_in_existing_operational_reminder_path(self):
        text = "Reminder: look into selling my old Apple Watches for parts. Anytime I'm not in a meeting"
        self.assertEqual(route_request(text).capability, "sheila_task")
        with patch.object(sheila_tasks.operational_store, "require_configured"), \
            patch.object(sheila_tasks.operational_store, "configured", return_value=True), \
            patch.object(sheila_tasks.operational_store, "list_reminders", return_value=[]), \
            patch.object(sheila_tasks.operational_store, "create_reminder") as create:
            reply = sheila_tasks.handle_request(text, NOW)
        self.assertIn("remind", reply.lower())
        create.assert_called_once()


if __name__ == "__main__":
    unittest.main()
