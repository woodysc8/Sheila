import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import Mock, patch

from agents.router import route_request
from agents.workflow import _calendar_range, _drive_query, _gmail_query, _is_due_today_task, handle_request
from integrations import asana
from integrations.google_auth import GoogleAuthError


class SheilaRoutingTests(unittest.TestCase):
    def test_general_request_stays_with_sheila(self):
        decision = route_request("Help me prepare for tomorrow")
        self.assertEqual(decision.agent, "Sheila")
        self.assertFalse(decision.delegation_ready)

    def test_financial_request_identifies_richard(self):
        decision = route_request("What tax deductions should I consider?")
        self.assertEqual(decision.agent, "Richard")
        self.assertFalse(decision.delegation_ready)

    def test_travel_request_identifies_travel(self):
        decision = route_request("Plan a trip to London")
        self.assertEqual(decision.agent, "Travel")
        self.assertFalse(decision.delegation_ready)

    def test_research_request_identifies_research(self):
        decision = route_request("Please do deep research on this market")
        self.assertEqual(decision.agent, "Research")
        self.assertFalse(decision.delegation_ready)

    def test_google_requests_select_sheila_read_only_capabilities(self):
        self.assertEqual(route_request("What's in my inbox?").capability, "gmail")
        self.assertEqual(route_request("What meetings do I have tomorrow?").capability, "calendar")
        self.assertEqual(route_request("Search my Drive for media list").capability, "drive")
        self.assertEqual(route_request("Am I free Friday afternoon?").capability, "calendar")
        self.assertEqual(route_request("What did Sarah say in her email?").capability, "gmail")
        self.assertEqual(route_request("Which clients does Team Networth serve?").capability, "drive")
        self.assertEqual(route_request("What Asana tasks are overdue?").capability, "asana")
        self.assertEqual(route_request("What's overdue?").capability, "asana")
        self.assertEqual(route_request("What tasks do I have due today?").capability, "asana")

    def test_google_query_extraction_handles_obvious_requests(self):
        self.assertEqual(_gmail_query("Did I get an email from John?"), "from:john")
        self.assertEqual(_gmail_query("What did Sarah say in her email?"), "from:sarah")
        start, end = _calendar_range("Am I free Friday afternoon?")
        self.assertEqual(start.weekday(), 4)
        self.assertEqual((start.hour, end.hour), (12, 17))

    def test_gmail_today_yesterday_week_and_recent_queries_are_date_bounded(self):
        current_date = date(2026, 8, 25)
        self.assertEqual(_gmail_query("What emails did I get today?", current_date), "in:inbox after:2026/08/25 before:2026/08/26")
        self.assertEqual(_gmail_query("What emails did I get yesterday?", current_date), "in:inbox after:2026/08/24 before:2026/08/25")
        self.assertEqual(_gmail_query("What emails did I get this week?", current_date), "in:inbox after:2026/08/24 before:2026/08/31")
        self.assertEqual(_gmail_query("Show me recent emails from Sarah.", current_date), "from:sarah after:2026/08/18 before:2026/08/26")

    def test_gmail_sender_preserves_date_constraint_and_explicit_operators(self):
        current_date = date(2026, 8, 25)
        self.assertEqual(_gmail_query("What did Sarah email me today?", current_date), "from:sarah after:2026/08/25 before:2026/08/26")
        self.assertIn("after:2026/08/01", _gmail_query("from:sarah after:2026/08/01", current_date))

    def test_google_workflow_passes_retrieved_context_to_existing_brain(self):
        sheila_brain = Mock(return_value="You have one relevant email.")
        with patch("agents.workflow.gmail.search_messages", return_value=[]):
            result = handle_request("What's in my inbox?", sheila_brain)
        self.assertEqual(result["route"]["agent"], "Sheila")
        self.assertIn("[GMAIL RESULTS]", sheila_brain.call_args.kwargs["context"])

    def test_google_context_is_source_labeled_and_calendar_bounded(self):
        sheila_brain = Mock(return_value="Calendar response")
        with patch("agents.workflow.calendar.get_events", return_value=[]):
            handle_request("What is on my calendar today and tomorrow?", sheila_brain)
        context = sheila_brain.call_args.kwargs["context"]
        self.assertIn("[CALENDAR RESULTS]", context)
        self.assertNotIn("[DRIVE RESULTS]", context)

    def test_drive_relationship_question_is_qualified_when_only_search_results_exist(self):
        sheila_brain = Mock(return_value="Qualified response")
        with patch("agents.workflow.drive.search_files", return_value=[{"id": "1", "name": "Dispatch overview", "mime_type": "text/plain", "modified_time": "", "web_view_link": "", "description": ""}]):
            handle_request("Which clients does Team Networth serve?", sheila_brain)
        context = sheila_brain.call_args.kwargs["context"]
        self.assertIn("[DRIVE RESULTS]", context)
        self.assertIn("[DRIVE RELATIONSHIP CAUTION]", context)
        self.assertIn("do not confirm", context)
        self.assertEqual(_drive_query("What clients exist in the Team Networth folder?"), "team networth")
        self.assertEqual(_drive_query("Which clients does Team Networth serve?"), "team networth")

    def test_calendar_today_and_tomorrow_uses_two_day_range(self):
        now = datetime(2026, 8, 25, 10, tzinfo=timezone.utc)
        start, end = _calendar_range("calendar today and tomorrow", now)
        self.assertEqual(start.date(), date(2026, 8, 25))
        self.assertEqual(end.date(), date(2026, 8, 27))

    def test_asana_overdue_records_reach_brain_with_all_records_preserved(self):
        tasks = [{"id": str(index), "name": f"Task {index}", "completed": False, "due_on": "2026-08-25", "due_at": None,
                  "assignee": "Sam", "project": "Ops", "workspace": "StreetCred", "permalink_url": f"https://asana/{index}"} for index in range(1, 5)]
        sheila_brain = Mock(return_value="Four overdue tasks.")
        with patch("agents.workflow.asana.get_overdue_tasks", return_value=tasks):
            handle_request("What Asana tasks are overdue?", sheila_brain)
        context = sheila_brain.call_args.kwargs["context"]
        self.assertIn("[ASANA RESULTS]", context)
        self.assertIn("successful with 4 matching overdue tasks", context)
        for task in tasks:
            self.assertIn(task["name"], context)
            self.assertIn(task["permalink_url"], context)

    def test_asana_zero_result_and_failure_are_distinct(self):
        sheila_brain = Mock(return_value="No overdue tasks.")
        with patch("agents.workflow.asana.get_overdue_tasks", return_value=[]):
            handle_request("Show me my overdue tasks", sheila_brain)
        self.assertIn("successful with zero matching overdue tasks", sheila_brain.call_args.kwargs["context"])

        unavailable_brain = Mock()
        with patch("agents.workflow.asana.get_overdue_tasks", side_effect=asana.AsanaError("down")):
            result = handle_request("Show me my overdue tasks", unavailable_brain)
        self.assertEqual(result["response"], "Asana isn't available right now.")
        unavailable_brain.assert_not_called()

    def test_unexpected_asana_programming_error_is_not_mislabeled_as_unavailable(self):
        with patch("agents.workflow.asana.get_overdue_tasks", side_effect=RuntimeError("formatting bug")):
            with self.assertRaisesRegex(RuntimeError, "formatting bug"):
                handle_request("Show me my overdue tasks", Mock())

    def test_asana_due_today_is_filtered_deterministically_without_brain(self):
        today = datetime.now().astimezone().date()
        tasks = [
            {"id": "today", "name": "Due today", "completed": False, "due_on": today.isoformat(), "due_at": None, "assignee": "", "project": "", "workspace": "", "permalink_url": ""},
            {"id": "completed", "name": "Completed", "completed": True, "due_on": today.isoformat(), "due_at": None, "assignee": "", "project": "", "workspace": "", "permalink_url": ""},
            {"id": "tomorrow", "name": "Tomorrow", "completed": False, "due_on": (today + timedelta(days=1)).isoformat(), "due_at": None, "assignee": "", "project": "", "workspace": "", "permalink_url": ""},
        ]
        self.assertTrue(_is_due_today_task(tasks[0], today))
        self.assertFalse(_is_due_today_task(tasks[1], today))
        sheila_brain = Mock()
        with patch("agents.workflow.asana.get_tasks", return_value=tasks):
            result = handle_request("What is due today in Asana?", sheila_brain)
        context = result["response"]
        self.assertIn("Due today", context)
        self.assertNotIn("Completed |", context)
        self.assertNotIn("Tomorrow", context)
        self.assertIn("[ASANA RESULTS]", context)
        sheila_brain.assert_not_called()

    def test_unspecified_asana_due_question_uses_live_due_today_records(self):
        today = datetime.now().astimezone().date().isoformat()
        tasks = [{"id": str(index), "name": f"Today {index}", "completed": False, "due_on": today,
                  "due_at": None, "assignee": "Sam", "project": "Ops", "workspace": "StreetCred",
                  "permalink_url": f"https://asana/{index}"} for index in range(1, 7)]
        brain_handler = Mock()
        with patch("agents.workflow.asana.get_tasks", return_value=tasks):
            result = handle_request("What is due in Asana?", brain_handler)
        self.assertIn("successful with 6 matching tasks due today", result["response"])
        for task in tasks:
            self.assertIn(task["name"], result["response"])
            self.assertIn(task["permalink_url"], result["response"])
        brain_handler.assert_not_called()

    def test_asana_due_today_zero_result_is_not_unavailable(self):
        brain_handler = Mock()
        with patch("agents.workflow.asana.get_tasks", return_value=[]):
            result = handle_request("What is due today in Asana?", brain_handler)
        self.assertIn("successful with zero matching tasks due today", result["response"])
        self.assertNotIn("isn't available", result["response"])
        brain_handler.assert_not_called()

    def test_calendar_events_context_is_authoritative_and_not_zero(self):
        event = {"title": "Standup", "start": "2026-08-26T09:00:00-04:00", "end": "2026-08-26T09:30:00-04:00", "location": ""}
        sheila_brain = Mock(return_value="You have Standup.")
        with patch("agents.workflow.calendar.get_events", return_value=[event]):
            handle_request("What is on my calendar today?", sheila_brain)
        context = sheila_brain.call_args.kwargs["context"]
        self.assertIn("successful with 1 event", context)
        self.assertIn("Standup", context)
        self.assertNotIn("No events found.", context)

    def test_google_failure_returns_service_specific_response_without_brain(self):
        sheila_brain = Mock()
        with patch("agents.workflow.gmail.search_messages", side_effect=GoogleAuthError("unavailable")):
            result = handle_request("Check my email", sheila_brain)
        self.assertEqual(result["response"], "Google email isn't available right now.")
        sheila_brain.assert_not_called()

    def test_planned_specialist_is_not_invoked(self):
        sheila_brain = Mock(return_value="Here is a careful general answer.")
        result = handle_request("Build me a travel itinerary", sheila_brain)

        sheila_brain.assert_called_once_with("Build me a travel itinerary")
        self.assertEqual(result["route"]["agent"], "Travel")
        self.assertIn("future specialist", result["response"])
        self.assertIn("I'll handle it myself", result["response"])


if __name__ == "__main__":
    unittest.main()
