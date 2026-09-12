import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from integrations.calendar import CalendarResult
import personal_calendar
import sheila_handler
from agents.router import route_request


NOW = datetime(2026, 9, 11, 12, tzinfo=ZoneInfo("America/New_York"))


class FakeGoogleCalendarAdapter:
    """In-memory Google API double; no local calendar store is involved."""
    events: dict[str, dict[str, object]] = {}
    creates = updates = deletes = finds = 0

    def __init__(self, *_args):
        pass

    @classmethod
    def reset(cls):
        cls.events, cls.creates, cls.updates, cls.deletes, cls.finds = {}, 0, 0, 0, 0

    def list_events(self, start, end, limit=20):
        def begins(event):
            value = event["start"]
            parsed = date.fromisoformat(value) if event["all_day"] else datetime.fromisoformat(value)
            return parsed < (end.date() if event["all_day"] else end)
        return CalendarResult(True, [event for event in self.events.values() if begins(event)][:limit])

    def find_event(self, event_id):
        self.__class__.finds += 1
        event = self.events.get(event_id)
        return CalendarResult(True, event) if event else CalendarResult(False, error="not found")

    def create_event(self, title, start, end, description="", location=""):
        event_id = f"google-{len(self.events) + 1}"
        all_day = isinstance(start, date) and not isinstance(start, datetime)
        event = {"id": event_id, "title": title, "start": start.isoformat(), "end": end.isoformat(),
                 "all_day": all_day, "timezone": None if all_day else "America/New_York",
                 "description": description, "location": location}
        self.events[event_id] = event
        self.__class__.creates += 1
        return CalendarResult(True, event)

    def update_event(self, event_id, title, start, end, description="", location=""):
        if event_id not in self.events:
            return CalendarResult(False, error="not found")
        self.events[event_id].update(title=title, start=start.isoformat() if hasattr(start, "isoformat") else start,
                                     end=end.isoformat() if hasattr(end, "isoformat") else end,
                                     description=description, location=location)
        self.__class__.updates += 1
        return CalendarResult(True, self.events[event_id])

    def delete_event(self, event_id):
        if event_id not in self.events:
            return CalendarResult(False, error="not found")
        del self.events[event_id]
        self.__class__.deletes += 1
        return CalendarResult(True, True)


class PersonalGoogleConversationTests(unittest.TestCase):
    def setUp(self):
        FakeGoogleCalendarAdapter.reset()
        personal_calendar._last_confirmed_event_id = None
        personal_calendar._ambiguous_event_ids = ()
        self.config = patch.object(personal_calendar.config, "SHEILA_PERSONAL_GOOGLE_CALENDAR_ID", "woodysc7@gmail.com")
        self.adapter = patch.object(personal_calendar, "GoogleCalendarAdapter", FakeGoogleCalendarAdapter)
        self.config.start()
        self.adapter.start()

    def tearDown(self):
        self.adapter.stop()
        self.config.stop()

    def test_create_read_followup_delete_is_verified_against_google(self):
        reply = personal_calendar.handle_personal_calendar_request("Add Sheila Calendar Test tomorrow from 12pm to 1pm", NOW)
        self.assertIn("Added", reply)
        self.assertEqual(FakeGoogleCalendarAdapter.creates, 1)
        self.assertIn("Sheila Calendar Test", personal_calendar.handle_personal_calendar_request("What do I have tomorrow?", NOW))
        deleted = personal_calendar.handle_personal_calendar_request("Remove that event", NOW)
        self.assertIn("Deleted", deleted)
        self.assertEqual(FakeGoogleCalendarAdapter.deletes, 1)
        self.assertEqual(FakeGoogleCalendarAdapter.events, {})

    def test_direct_delete_and_false_existence_use_google_only(self):
        self.assertEqual(route_request("Is Zach Bryan at Gillette Stadium on October 2nd on my calendar?").capability, "personal_calendar")
        self.assertIn("not currently", personal_calendar.handle_personal_calendar_request("Is Zach Bryan at Gillette Stadium on October 2nd on my calendar?", NOW))
        added = personal_calendar.handle_personal_calendar_request("Add Zach Bryan at Gillette Stadium on October 2nd", NOW)
        self.assertIn("Added", added)
        self.assertEqual(FakeGoogleCalendarAdapter.creates, 1)
        self.assertIn("currently", personal_calendar.handle_personal_calendar_request("Is Zach Bryan at Gillette Stadium on October 2nd on my calendar?", NOW))
        self.assertIn("Deleted", personal_calendar.handle_personal_calendar_request("Delete Zach Bryan tomorrow", NOW.replace(month=10, day=1)))
        self.assertEqual(FakeGoogleCalendarAdapter.events, {})

    def test_active_event_detail_followups_re_read_google_and_clear_after_delete(self):
        personal_calendar.handle_personal_calendar_request("Add Zach Bryan at Gillette Stadium on October 2nd", NOW)
        self.assertIn("currently", personal_calendar.handle_personal_calendar_request(
            "Is Zach Bryan on my calendar?", NOW
        ))
        self.assertIn("October 2", personal_calendar.handle_personal_calendar_request("When?", NOW))
        self.assertIn("all-day", personal_calendar.handle_personal_calendar_request("What time?", NOW))
        self.assertIn("Friday", personal_calendar.handle_personal_calendar_request("What day?", NOW))
        self.assertGreaterEqual(FakeGoogleCalendarAdapter.finds, 3)
        self.assertIn("Deleted", personal_calendar.handle_personal_calendar_request("Remove that event", NOW))
        self.assertEqual(personal_calendar.handle_personal_calendar_request("When?", NOW), "Which calendar event do you mean?")

    def test_ambiguous_candidates_can_be_listed_selected_and_deleted(self):
        personal_calendar.handle_personal_calendar_request("Add Zach Bryan on October 2nd", NOW)
        personal_calendar.handle_personal_calendar_request("Add Zach Bryan on October 3rd", NOW)
        FakeGoogleCalendarAdapter.events["google-1"]["location"] = "Gillette Stadium"
        FakeGoogleCalendarAdapter.events["google-2"]["location"] = "Other venue"
        reply = personal_calendar.handle_personal_calendar_request("Is Zach Bryan on my calendar?", NOW)
        self.assertIn("There are 2 matching events", reply)
        self.assertIn("October 2", reply)
        self.assertIn("Gillette Stadium", reply)
        for question in ("What events?", "Which ones?", "Show me them", "Show me the events", "What are they?"):
            self.assertIn("There are 2 matching events", personal_calendar.handle_personal_calendar_request(question, NOW))
        self.assertIn("Selected", personal_calendar.handle_personal_calendar_request("The first one", NOW))
        self.assertIn("October 2", personal_calendar.handle_personal_calendar_request("When?", NOW))
        self.assertIn("Deleted", personal_calendar.handle_personal_calendar_request("Remove that event", NOW))
        self.assertEqual(set(FakeGoogleCalendarAdapter.events), {"google-2"})

    def test_ambiguous_date_selection_requires_one_candidate_for_that_date(self):
        personal_calendar.handle_personal_calendar_request("Add Zach Bryan on October 2nd", NOW)
        personal_calendar.handle_personal_calendar_request("Add Zach Bryan on October 3rd", NOW)
        personal_calendar.handle_personal_calendar_request("Is Zach Bryan on my calendar?", NOW)
        self.assertIn("Selected", personal_calendar.handle_personal_calendar_request("The October 3rd one", NOW))
        self.assertIn("October 3", personal_calendar.handle_personal_calendar_request("When?", NOW))

    def test_missing_ambiguous_reference_requires_clarification(self):
        self.assertEqual(personal_calendar.handle_personal_calendar_request("When?", NOW), "Which calendar event do you mean?")
        self.assertEqual(personal_calendar.handle_personal_calendar_request("What events?", NOW), "Which calendar event do you mean?")

    def test_new_calendar_topic_clears_ambiguous_candidates(self):
        personal_calendar.handle_personal_calendar_request("Add Zach Bryan on October 2nd", NOW)
        personal_calendar.handle_personal_calendar_request("Add Zach Bryan on October 3rd", NOW)
        personal_calendar.handle_personal_calendar_request("Is Zach Bryan on my calendar?", NOW)
        personal_calendar.handle_personal_calendar_request("What do I have tomorrow?", NOW)
        self.assertEqual(personal_calendar.handle_personal_calendar_request("What events?", NOW), "Which calendar event do you mean?")

    def test_handler_detail_followup_does_not_read_or_write_sam2(self):
        personal_calendar.handle_personal_calendar_request("Add Zach Bryan on October 2nd", NOW)
        personal_calendar.handle_personal_calendar_request("Is Zach Bryan on my calendar?", NOW)
        with patch.object(sheila_handler.memory, "recall") as recall, \
             patch.object(sheila_handler.memory, "remember") as remember, \
             patch.object(sheila_handler.orchestration, "execute_with_center") as center, \
             patch.object(sheila_handler.memory, "log_exchange"):
            reply = sheila_handler.process_message("When?")
        self.assertIn("October 2", reply)
        recall.assert_not_called()
        remember.assert_not_called()
        center.assert_not_called()

    def test_batch_all_day_birthday_and_idempotency_report_actual_google_results(self):
        batch = """October 2: Zach Bryan at Gillette Stadium.
October 21-23: company retreat in Philadelphia via Amtrak.
November 18-22: PTO / Dominican Republic trip with Nora.
This Friday: Nora arrives.
Saturday: Nora leaves before dinner.
Saturday morning: doctor appointment."""
        reply = personal_calendar._create_definite_event_candidates(batch, NOW)
        self.assertIn("Added", reply)
        self.assertGreater(FakeGoogleCalendarAdapter.creates, 0)
        again = personal_calendar._create_definite_event_candidates(batch, NOW)
        self.assertIn("Already on your personal Google Calendar", again)
        birthday = personal_calendar.handle_personal_calendar_request("Add Sam birthday November 18th as an all-day personal Google Calendar event", NOW)
        self.assertIn("all-day", birthday)
        self.assertTrue(any(event["title"] == "Sam birthday" and event["all_day"] for event in FakeGoogleCalendarAdapter.events.values()))

    def test_update_and_failure_do_not_claim_success(self):
        personal_calendar.handle_personal_calendar_request("Add Sheila Calendar Test tomorrow at 12pm", NOW)
        self.assertIn("Moved", personal_calendar.handle_personal_calendar_request("Move Sheila Calendar Test to 1 PM", NOW))
        self.assertEqual(FakeGoogleCalendarAdapter.updates, 1)
        with patch.object(FakeGoogleCalendarAdapter, "delete_event", return_value=CalendarResult(False, error="down")):
            self.assertIn("couldn't delete", personal_calendar.handle_personal_calendar_request("Delete Sheila Calendar Test tomorrow", NOW))


if __name__ == "__main__":
    unittest.main()
