import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from integrations import calendar
from integrations.calendar import GoogleCalendarAdapter
import personal_calendar


class GoogleCalendarAdapterTests(unittest.TestCase):
    def setUp(self):
        self.service = MagicMock()
        self.adapter = GoogleCalendarAdapter("woodysc7-personal@example.com", lambda *_: self.service)
        self.start = datetime(2026, 9, 11, 13, tzinfo=timezone.utc)
        self.end = self.start + timedelta(hours=1)
        self.timed_api_event = {"id": "google-123", "summary": "Dentist", "start": {"dateTime": "2026-09-11T13:00:00Z"}, "end": {"dateTime": "2026-09-11T14:00:00Z"}}

    def test_list_events_uses_personal_calendar_and_normalizes_timed_events(self):
        self.service.events().list().execute.return_value = {"items": [self.timed_api_event]}
        result = self.adapter.list_events(self.start, self.end)
        self.assertTrue(result.success)
        self.assertEqual(result.value[0]["id"], "google-123")
        self.assertEqual(result.value[0]["start"], "2026-09-11T09:00:00-04:00")
        self.assertEqual(self.service.events().list.call_args.kwargs["calendarId"], "woodysc7-personal@example.com")

    def test_create_is_successful_only_after_google_confirms_and_uses_new_york(self):
        self.service.events().insert().execute.return_value = self.timed_api_event
        result = self.adapter.create_event("Dentist", self.start, self.end)
        self.assertTrue(result.success)
        body = self.service.events().insert.call_args.kwargs["body"]
        self.assertEqual(body["start"]["dateTime"], "2026-09-11T09:00:00-04:00")
        self.assertEqual(body["start"]["timeZone"], "America/New_York")
        self.assertEqual(result.value["id"], "google-123")

    def test_update_preserves_google_id_and_delete_requires_api_success(self):
        updated = {**self.timed_api_event, "summary": "Dentist (moved)"}
        self.service.events().update().execute.return_value = updated
        updated_result = self.adapter.update_event("google-123", "Dentist (moved)", self.start, self.end)
        self.assertTrue(updated_result.success)
        self.assertEqual(self.service.events().update.call_args.kwargs["eventId"], "google-123")
        self.service.events().delete().execute.side_effect = RuntimeError("403 forbidden")
        deleted_result = self.adapter.delete_event("google-123")
        self.assertFalse(deleted_result.success)
        self.assertIn("deletion failed", deleted_result.error)

    def test_all_day_event_preserves_exclusive_end_date(self):
        all_day = {"id": "holiday", "summary": "Vacation", "start": {"date": "2026-09-11"}, "end": {"date": "2026-09-13"}}
        self.service.events().insert().execute.return_value = all_day
        result = self.adapter.create_event("Vacation", date(2026, 9, 11), date(2026, 9, 13))
        self.assertTrue(result.success)
        self.assertTrue(result.value["all_day"])
        self.assertEqual(result.value["end"], "2026-09-13")
        body = self.service.events().insert.call_args.kwargs["body"]
        self.assertEqual(body["start"], {"date": "2026-09-11"})
        self.assertEqual(body["end"], {"date": "2026-09-13"})

    def test_missing_configuration_is_actionable(self):
        with patch.object(calendar.config, "SHEILA_PERSONAL_GOOGLE_CALENDAR_ID", ""):
            result = GoogleCalendarAdapter(service_factory=lambda *_: self.service).list_events(self.start, self.end)
        self.assertFalse(result.success)
        self.assertIn("SHEILA_PERSONAL_GOOGLE_CALENDAR_ID", result.error)

    def test_google_api_failure_is_not_reported_as_create_success(self):
        self.service.events().insert().execute.side_effect = RuntimeError("network unavailable")
        result = self.adapter.create_event("Dentist", self.start, self.end)
        self.assertFalse(result.success)
        self.assertIsNone(result.value)
        self.assertIn("creation failed", result.error)

    def test_find_event_and_list_calendars(self):
        self.service.events().get().execute.return_value = self.timed_api_event
        self.service.calendarList().list().execute.return_value = {"items": [{"id": "woodysc7-personal@example.com", "summary": "Personal", "primary": True}]}
        found = self.adapter.find_event("google-123")
        calendars = self.adapter.list_calendars()
        self.assertTrue(found.success)
        self.assertEqual(found.value["id"], "google-123")
        self.assertTrue(calendars.success)
        self.assertTrue(calendars.value[0]["primary"])

    def test_list_events_follows_google_page_tokens(self):
        second = {**self.timed_api_event, "id": "google-456"}
        self.service.events.return_value.list.return_value.execute.side_effect = [
            {"items": [self.timed_api_event], "nextPageToken": "next"},
            {"items": [second]},
        ]
        result = self.adapter.list_events(self.start, self.end + timedelta(days=1), limit=10)
        self.assertTrue(result.success)
        self.assertEqual([event["id"] for event in result.value], ["google-123", "google-456"])
        self.assertEqual(self.service.events().list.call_count, 2)

    def test_conversational_create_uses_google_adapter_when_calendar_id_is_configured(self):
        adapter = MagicMock()
        adapter.create_event.return_value = calendar.CalendarResult(
            True, {"id": "google-1", "title": "Sheila Calendar Test"}
        )
        with patch.object(personal_calendar.config, "SHEILA_PERSONAL_GOOGLE_CALENDAR_ID", "woodysc7@gmail.com"), \
             patch.object(personal_calendar, "GoogleCalendarAdapter", return_value=adapter):
            response = personal_calendar.handle_personal_calendar_request(
                "Add a test event to my personal calendar tomorrow called Sheila Calendar Test 3-4pm",
                datetime(2026, 9, 11, 12, tzinfo=timezone.utc),
            )
        self.assertIn("Added", response)
        adapter.create_event.assert_called_once()
        self.assertEqual(adapter.create_event.call_args.args[0], "Sheila Calendar Test")
        self.assertEqual(adapter.create_event.call_args.args[1].hour, 15)
        self.assertEqual(adapter.create_event.call_args.args[2].hour, 16)


if __name__ == "__main__":
    unittest.main()
