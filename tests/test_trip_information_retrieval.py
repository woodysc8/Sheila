import unittest
from unittest.mock import patch

from agents.router import route_request
import personal_calendar
import sheila_handler


FLIGHT = {
    "id": "google-flight-sdq",
    "title": "Flight to Santo Domingo (DM 2003)",
    "start": "2026-11-18T11:15:00-05:00",
    "end": "2026-11-18T15:37:00-05:00",
    "all_day": False,
    "location": "BOS → SDQ",
    "description": "",
}
RETURN_FLIGHT = {
    "id": "google-flight-bos",
    "title": "Flight to Boston (DM 2004)",
    "start": "2026-11-22T15:29:00-05:00",
    "end": "2026-11-22T19:45:00-05:00",
    "all_day": False,
    "location": "SDQ → BOS",
    "description": "",
}


class TripInformationRetrievalTests(unittest.TestCase):
    def _reply(self, question, events, memories):
        with patch.object(personal_calendar, "get_personal_calendar_events", return_value=events) as calendar_read, \
             patch.object(sheila_handler.memory, "recall", return_value=memories):
            reply = sheila_handler._calendar_and_memory_lookup(question)
        return reply, calendar_read

    def test_dominican_republic_queries_match_santo_domingo_flight(self):
        for question in (
            "When am I going to the Dominican Republic?",
            "When am I going to DR?",
            "What are my plans for the Dominican Republic?",
            "When is my trip to Santo Domingo?",
            "What do I have planned for the DR?",
        ):
            reply, calendar_read = self._reply(question, [FLIGHT, RETURN_FLIGHT], [])
            self.assertIn("November 18–22", reply)
            self.assertIn("Santo Domingo", reply)
            self.assertIn("Google Calendar", reply)
            self.assertTrue(calendar_read.called)
            self.assertEqual(route_request(question).agent, "Sheila")

    def test_unrelated_destination_does_not_match_dominican_flight(self):
        self.assertIsNone(personal_calendar.find_existing_trip_events("When am I going to London?"))

    def test_calendar_result_without_sam2_result_is_still_reported(self):
        reply, _ = self._reply("When am I going to the Dominican Republic?", [FLIGHT], [])
        self.assertIn("one matching trip event", reply)
        self.assertIn("No relevant remembered context", reply)

    def test_sam2_result_without_calendar_result_is_still_reported(self):
        memory_fact = {"content": "Sam plans a Dominican Republic trip with Nora November 18–22, 2026."}
        reply, _ = self._reply("When am I going to the Dominican Republic?", [], [memory_fact])
        self.assertIn("couldn't find matching Dominican Republic trip events", reply)
        self.assertIn("Dominican Republic trip", reply)

    def test_both_calendar_and_sam2_results_are_labeled(self):
        memory_fact = {"content": "Sam plans a Dominican Republic trip with Nora November 18–22, 2026."}
        reply, _ = self._reply("When is my trip to Santo Domingo?", [FLIGHT], [memory_fact])
        self.assertIn("Calendar (Google Calendar)", reply)
        self.assertIn("Remembered context (Sam 2)", reply)
        self.assertIn("one matching trip event", reply)
        self.assertIn("Dominican Republic trip", reply)

    def test_existing_trip_phrasings_read_calendar_and_never_invoke_travel(self):
        for question in (
            "When am I going to the Dominican Republic?",
            "When is my trip to the Dominican Republic?",
            "When do I fly to the Dominican Republic?",
            "Do I have a flight on November 18th to the Dominican Republic?",
            "What are my Dominican Republic travel dates?",
        ):
            with self.subTest(question=question), \
                 patch.object(personal_calendar, "get_personal_calendar_events", return_value=[FLIGHT, RETURN_FLIGHT]) as calendar_read, \
                 patch.object(sheila_handler.memory, "recall", return_value=[]), \
                 patch.object(sheila_handler.memory, "log_exchange"), \
                 patch.object(sheila_handler, "handle_request") as workflow_request:
                reply = sheila_handler.process_message(question)
            self.assertTrue(calendar_read.called)
            workflow_request.assert_not_called()
            self.assertEqual(route_request(question).agent, "Sheila")
            self.assertIn("November 18–22", reply)

    def test_travel_operations_remain_future_specialist_intents(self):
        for question in ("Find me flights to the Dominican Republic", "Plan a trip to the Dominican Republic"):
            decision = route_request(question)
            self.assertEqual(decision.agent, "Travel")
            self.assertEqual(decision.intent, "travel_planning_or_research")

    def test_no_matching_trip_events_does_not_fabricate_dates(self):
        reply, _ = self._reply("When am I going to the Dominican Republic?", [], [])
        self.assertIn("couldn't find matching Dominican Republic trip events", reply)
        self.assertNotIn("November 18", reply)
        self.assertNotIn("November 22", reply)


if __name__ == "__main__":
    unittest.main()
