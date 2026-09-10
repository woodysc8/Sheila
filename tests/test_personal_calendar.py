import http.client
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import calendar_store
import personal_calendar
import zavu_webhook
from agents.router import route_request
from agents.workflow import handle_request


EASTERN = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 9, 12, tzinfo=EASTERN)


class PersonalCalendarStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(calendar_store.config, "DB_PATH", os.path.join(self.temp_dir.name, "calendar.db"))
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_create_retrieve_range_update_delete_and_reopen(self):
        event = calendar_store.create_event(
            "Trivia Night", "2026-09-10T20:00:00", "2026-09-10T22:00:00",
            timezone="America/New_York", location="The pub", description="Bring team name.",
        )
        self.assertEqual(event["timezone"], "America/New_York")
        self.assertEqual(calendar_store.get_event(event["id"])["title"], "Trivia Night")
        listed = calendar_store.list_events("2026-09-10T00:00:00", "2026-09-11T00:00:00", "America/New_York")
        self.assertEqual([item["id"] for item in listed], [event["id"]])

        updated = calendar_store.update_event(event["id"], start="2026-09-10T20:30:00", end="2026-09-10T22:30:00", timezone="America/New_York")
        self.assertTrue(updated["start"].startswith("2026-09-10T20:30:00-04:00"))
        self.assertEqual(calendar_store.get_event(event["id"])["start"], updated["start"])
        self.assertTrue(calendar_store.delete_event(event["id"]))
        self.assertIsNone(calendar_store.get_event(event["id"]))

        reopened = calendar_store._connect()
        reopened.close()
        self.assertEqual(calendar_store.list_events("2026-09-10T00:00:00", "2026-09-11T00:00:00", "America/New_York"), [])

    def test_natural_update_survives_fresh_connection(self):
        event = personal_calendar.handle_personal_calendar_request("Nora is coming Friday at 7.", NOW)
        self.assertEqual(event, "Got it.")
        stored = calendar_store.list_events("2026-09-11T00:00:00", "2026-09-12T00:00:00", "America/New_York")[0]
        response = personal_calendar.handle_personal_calendar_request("Nora is actually coming at 8.", NOW)
        self.assertIn("8:00 PM", response)
        reopened = calendar_store._connect()
        reopened.close()
        read_back = calendar_store.get_event(stored["id"])
        listed = calendar_store.list_events("2026-09-11T00:00:00", "2026-09-12T00:00:00", "America/New_York")
        self.assertIn("T20:00:00-04:00", read_back["start"])
        self.assertIn("T21:00:00-04:00", read_back["end"])
        self.assertEqual(listed[0]["start"], read_back["start"])

    def test_timezone_conversion_and_invalid_data(self):
        event = calendar_store.create_event(
            "UTC event", "2026-09-10T00:00:00Z", "2026-09-10T01:00:00Z", timezone="America/New_York"
        )
        self.assertTrue(event["start"].startswith("2026-09-09T20:00:00-04:00"))
        with self.assertRaises(calendar_store.CalendarError):
            calendar_store.create_event("", "2026-09-10T09:00:00", "2026-09-10T10:00:00", timezone="America/New_York")
        with self.assertRaises(calendar_store.CalendarError):
            calendar_store.create_event("Backwards", "2026-09-10T10:00:00", "2026-09-10T09:00:00", timezone="America/New_York")
        with self.assertRaises(calendar_store.CalendarError):
            calendar_store.parse_datetime("2026-09-10T09:00:00")


class PersonalCalendarInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(calendar_store.config, "DB_PATH", os.path.join(self.temp_dir.name, "calendar.db"))
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_route_and_explicit_commands_use_personal_calendar(self):
        self.assertEqual(route_request("What is on my personal calendar tomorrow?").capability, "personal_calendar")
        self.assertEqual(route_request("Add trivia night tomorrow at 8 PM.").capability, "personal_calendar")
        self.assertEqual(route_request("Move trivia night to 8:30.").capability, "personal_calendar")
        self.assertEqual(route_request("Cancel trivia night.").capability, "personal_calendar")
        added = personal_calendar.handle_personal_calendar_request("Add trivia night tomorrow at 8 PM.", NOW)
        self.assertIn("Added trivia night", added)
        read = personal_calendar.handle_personal_calendar_request("What do I have on my personal calendar tomorrow?", NOW)
        self.assertIn("trivia night", read.lower())
        moved = personal_calendar.handle_personal_calendar_request("Move trivia night to 8:30.", NOW)
        self.assertIn("8:30 PM", moved)
        deleted = personal_calendar.handle_personal_calendar_request("Cancel trivia night.", NOW)
        self.assertIn("Deleted", deleted)
        self.assertEqual(personal_calendar.handle_personal_calendar_request("What is on my personal calendar tomorrow?", NOW), "No personal-calendar events found.")

    def test_ambiguous_mutation_does_not_change_calendar(self):
        response = personal_calendar.handle_personal_calendar_request("Maybe I should put dinner with Sarah on Friday.", NOW)
        self.assertIn("Please specify", response)
        self.assertEqual(calendar_store.list_events("2026-09-11T00:00:00", "2026-09-12T00:00:00", "America/New_York"), [])

    def test_update_preserves_existing_meridiem_when_time_is_ambiguous(self):
        personal_calendar.handle_personal_calendar_request("Add trivia night tomorrow at 8 PM.", NOW)
        personal_calendar.handle_personal_calendar_request("Move trivia night to 8:30.", NOW)
        event = calendar_store.list_events("2026-09-10T00:00:00", "2026-09-11T00:00:00", "America/New_York")[0]
        self.assertIn("T20:30:00-04:00", event["start"])

    def test_update_honors_explicit_am_and_pm(self):
        personal_calendar.handle_personal_calendar_request("Add morning briefing tomorrow at 8 AM.", NOW)
        personal_calendar.handle_personal_calendar_request("Move morning briefing to 8:30 PM.", NOW)
        morning = calendar_store.list_events("2026-09-10T00:00:00", "2026-09-11T00:00:00", "America/New_York")[0]
        self.assertIn("T20:30:00-04:00", morning["start"])

        personal_calendar.handle_personal_calendar_request("Add evening briefing tomorrow at 8 PM.", NOW)
        personal_calendar.handle_personal_calendar_request("Move evening briefing to 8:30 AM.", NOW)
        events = calendar_store.list_events("2026-09-10T00:00:00", "2026-09-11T00:00:00", "America/New_York")
        evening = next(event for event in events if event["title"] == "evening briefing")
        self.assertIn("T08:30:00-04:00", evening["start"])

    def test_definite_natural_plan_creates_event_without_calendar_phrase(self):
        self.assertEqual(route_request("Nora is coming Thursday at 7.").capability, "personal_calendar")
        response = personal_calendar.handle_personal_calendar_request("Nora is coming Thursday at 7.", NOW)
        self.assertEqual(response, "Got it.")
        events = calendar_store.list_events("2026-09-10T00:00:00", "2026-09-11T00:00:00", "America/New_York")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "Nora coming")
        self.assertIn("T19:00:00-04:00", events[0]["start"])
        self.assertIn("T20:00:00-04:00", events[0]["end"])

    def test_tentative_historical_and_hypothetical_plans_do_not_create(self):
        for text in (
            "Nora might come Thursday at 7.",
            "Maybe dinner Friday at 7.",
            "I'm thinking about going to Boston Saturday at 8.",
            "If Nora comes Thursday at 7, we'll go out.",
            "I went to trivia last Friday at 8.",
        ):
            self.assertFalse(route_request(text).capability == "personal_calendar", text)
            personal_calendar.handle_personal_calendar_request(text, NOW)
        self.assertEqual(calendar_store.list_events("2026-09-01T00:00:00", "2026-09-30T00:00:00", "America/New_York"), [])

    def test_natural_lookup_and_cancellation(self):
        personal_calendar.handle_personal_calendar_request("Dinner with Mom Saturday at 6.", NOW)
        self.assertEqual(route_request("What do I have Saturday?").capability, "personal_calendar")
        response = personal_calendar.handle_personal_calendar_request("What do I have Saturday?", NOW)
        self.assertIn("Dinner with Mom", response)
        personal_calendar.handle_personal_calendar_request("Dinner with Mom is off.", NOW)
        self.assertEqual(calendar_store.list_events("2026-09-12T00:00:00", "2026-09-13T00:00:00", "America/New_York"), [])

    def test_natural_update_and_named_lookup_preserve_event_context(self):
        personal_calendar.handle_personal_calendar_request("Nora is coming Thursday at 7.", NOW)
        response = personal_calendar.handle_personal_calendar_request("Nora is actually coming at 8.", NOW)
        self.assertIn("8:00 PM", response)
        lookup = personal_calendar.handle_personal_calendar_request("When is Nora?", NOW)
        self.assertIn("Nora coming", lookup)
        event = calendar_store.list_events("2026-09-10T00:00:00", "2026-09-11T00:00:00", "America/New_York")[0]
        self.assertIn("T20:00:00-04:00", event["start"])

    def test_natural_friday_update_persists_and_cleans_title(self):
        personal_calendar.handle_personal_calendar_request("Nora is coming Friday at 7.", NOW)
        created = calendar_store.list_events("2026-09-11T00:00:00", "2026-09-12T00:00:00", "America/New_York")[0]
        self.assertEqual(created["title"], "Nora coming")

        response = personal_calendar.handle_personal_calendar_request("Nora is actually coming at 8.", NOW)
        self.assertIn("8:00 PM", response)

        persisted = calendar_store.get_event(created["id"])
        self.assertEqual(persisted["title"], "Nora coming")
        self.assertIn("T20:00:00-04:00", persisted["start"])
        self.assertIn("T21:00:00-04:00", persisted["end"])
        listed = calendar_store.list_events("2026-09-11T00:00:00", "2026-09-12T00:00:00", "America/New_York")
        self.assertEqual([(event["id"], event["start"], event["end"]) for event in listed],
                         [(created["id"], persisted["start"], persisted["end"])])

    def test_natural_update_matches_normalized_subject_and_rejects_ambiguity(self):
        personal_calendar.handle_personal_calendar_request("Nora is coming Friday at 7.", NOW)
        response = personal_calendar.handle_personal_calendar_request("Nora is actually coming at 8.", NOW)
        self.assertIn("8:00 PM", response)
        event = calendar_store.list_events("2026-09-11T00:00:00", "2026-09-12T00:00:00", "America/New_York")[0]
        self.assertIn("T20:00:00-04:00", event["start"])

        personal_calendar.handle_personal_calendar_request("Nora is coming Saturday at 7.", NOW)
        response = personal_calendar.handle_personal_calendar_request("Nora is actually coming at 9.", NOW)
        self.assertIn("Which matching", response)
        events = calendar_store.list_events("2026-09-11T00:00:00", "2026-09-14T00:00:00", "America/New_York")
        self.assertIn("T20:00:00-04:00", events[0]["start"])
        self.assertIn("T19:00:00-04:00", events[1]["start"])

    def test_tentative_and_historical_followup_do_not_mutate(self):
        personal_calendar.handle_personal_calendar_request("Nora is coming Friday at 7.", NOW)
        personal_calendar.handle_personal_calendar_request("Maybe Nora is actually coming at 8.", NOW)
        personal_calendar.handle_personal_calendar_request("I went to Nora actually coming at 9 last Friday.", NOW)
        event = calendar_store.list_events("2026-09-11T00:00:00", "2026-09-12T00:00:00", "America/New_York")[0]
        self.assertIn("T19:00:00-04:00", event["start"])

    def test_workflow_handles_natural_plan_without_llm(self):
        response_handler = Mock()
        result = handle_request("Nora is coming Thursday at 7.", response_handler)
        self.assertEqual(result["response"], "Got it.")
        response_handler.assert_not_called()


class PersonalCalendarApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(calendar_store.config, "DB_PATH", os.path.join(self.temp_dir.name, "calendar.db"))
        self.db_patch.start()
        self.server = zavu_webhook.ThreadingHTTPServer(("127.0.0.1", 0), zavu_webhook.ZavuWebhookHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=1)
        self.server.server_close()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def request(self, method, path, payload=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1])
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"} if payload is not None else {}
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        content = response.read()
        connection.close()
        return response.status, json.loads(content) if content else None

    def test_browser_page_and_crud_api(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1])
        connection.request("GET", "/calendar")
        response = connection.getresponse()
        page = response.read().decode()
        connection.close()
        self.assertEqual(response.status, 200)
        self.assertIn("Sheila Personal Calendar", page)

        payload = {"title": "Trivia Night", "start": "2026-09-10T20:00:00", "end": "2026-09-10T22:00:00", "timezone": "America/New_York"}
        status, created = self.request("POST", "/api/calendar/events", payload)
        self.assertEqual(status, 201)
        status, result = self.request("GET", "/api/calendar/events?start=2026-09-10T00%3A00%3A00&end=2026-09-11T00%3A00%3A00&timezone=America%2FNew_York")
        self.assertEqual(status, 200)
        self.assertEqual(result["events"][0]["title"], "Trivia Night")
        status, result = self.request("GET", f"/api/calendar/events/{created['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(result["title"], "Trivia Night")
        status, updated = self.request("PATCH", f"/api/calendar/events/{created['id']}", {"start": "2026-09-10T20:30:00", "end": "2026-09-10T22:30:00", "timezone": "America/New_York"})
        self.assertEqual(status, 200)
        self.assertIn("20:30:00-04:00", updated["start"])
        status, _ = self.request("DELETE", f"/api/calendar/events/{created['id']}")
        self.assertEqual(status, 204)

    def test_malformed_api_data_returns_bad_request(self):
        status, body = self.request("POST", "/api/calendar/events", {"title": "Missing dates"})
        self.assertEqual(status, 400)
        self.assertIn("Datetime is required", body["error"])


if __name__ == "__main__":
    unittest.main()
