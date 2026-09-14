import unittest
from unittest.mock import Mock, patch

from agents.router import route_request
from agents.workflow import handle_request
import orchestration
from orchestration_contract import ExecutionResult
from sam2_memory_client import Sam2MemorySearchError


class TravelMemoryWorkflowTests(unittest.TestCase):
    def _context(self):
        return orchestration.request_context(
            "Plan me a warm beach trip in November.",
            action="specialist_delegation",
            target="center",
            conversation_context={
                "conversation_history": ["private conversation"],
                "user_profile": {"home_airport": "BOS"},
                "oauth_token": "private-token",
            },
            relevant_memory=[{"content": "unselected memory"}],
        )

    def test_routed_travel_retrieves_and_selectively_passes_memory(self):
        task = "Plan me a warm beach trip in November."
        decision = route_request(task)
        memories = [
            {"id": "m-1", "category": "travel", "content": "Sam prefers nonstop flights.", "metadata": {"private": True}},
            {"id": "m-2", "category": "travel", "content": "Sam prefers warm beaches in November."},
            {"id": "m-3", "category": "travel", "content": ""},
        ]
        delegated = ExecutionResult("request-1", "succeeded", {"boundary_reached": True}, "center")
        context = self._context()
        with patch.object(orchestration, "retrieve_relevant_memory", return_value=memories) as retrieve, \
                patch.object(orchestration, "delegate_routed_specialist_with_center", return_value=delegated) as delegate:
            result = orchestration.delegate_routed_travel_with_memory(context, decision.agent, task)

        self.assertEqual(decision.agent, "Travel")
        self.assertIs(result, delegated)
        retrieve.assert_called_once_with(query=task, category="travel", limit=6)
        delegate.assert_called_once_with(
            context,
            "Travel",
            task,
            relevant_context={"memory_context": [
                {"category": "travel", "content": "Sam prefers nonstop flights."},
                {"category": "travel", "content": "Sam prefers warm beaches in November."},
            ]},
            constraints=(),
            authority_scope="read",
        )

    def test_memory_failure_still_delegates_travel_without_context(self):
        task = "Find me cheap flights to London."
        context = self._context()
        delegated = ExecutionResult("request-1", "succeeded", {"boundary_reached": True}, "center")
        with patch.object(orchestration, "retrieve_relevant_memory", side_effect=Sam2MemorySearchError("down")), \
                patch.object(orchestration, "delegate_routed_specialist_with_center", return_value=delegated) as delegate:
            result = orchestration.delegate_routed_travel_with_memory(context, "Travel", task)

        self.assertIs(result, delegated)
        self.assertEqual(delegate.call_args.kwargs["relevant_context"], {})

    def test_existing_trip_information_never_enters_travel_memory_workflow(self):
        question = "When is my Dominican Republic trip?"
        self.assertEqual(route_request(question).agent, "Sheila")
        with patch.object(orchestration, "retrieve_relevant_memory") as retrieve, \
                patch.object(orchestration, "delegate_routed_specialist_with_center") as delegate:
            handle_request(question, Mock(return_value="Calendar answer."))

        retrieve.assert_not_called()
        delegate.assert_not_called()

    def test_ordinary_request_does_not_retrieve_travel_memory(self):
        with patch.object(orchestration, "retrieve_relevant_memory") as retrieve:
            handle_request("Help me prepare for tomorrow", Mock(return_value="Prepared."))

        retrieve.assert_not_called()

    def test_non_travel_route_never_retrieves_memory_or_delegates(self):
        context = self._context()
        with patch.object(orchestration, "retrieve_relevant_memory") as retrieve, \
                patch.object(orchestration, "delegate_routed_specialist_with_center") as delegate:
            result = orchestration.delegate_routed_travel_with_memory(context, "Richard", "What tax deductions apply?")

        retrieve.assert_not_called()
        delegate.assert_not_called()
        self.assertEqual(result.status, "failed")


if __name__ == "__main__":
    unittest.main()
