import unittest
from unittest.mock import Mock, patch

from agents.router import route_request
from agents.workflow import handle_request
import orchestration
from orchestration_contract import ExecutionResult


class SpecialistExecutionDecisionTests(unittest.TestCase):
    def _context(self):
        return orchestration.request_context(
            "Plan a trip to Costa Rica.",
            action="specialist_execution",
            target="center",
            conversation_context={
                "conversation_history": ["private conversation"],
                "user_profile": {"home_airport": "BOS"},
                "oauth_token": "private-token",
            },
        )

    def test_travel_is_a_candidate_but_is_explicitly_disabled(self):
        decision = orchestration.specialist_execution_decision("Travel")

        self.assertTrue(decision.is_specialist_candidate)
        self.assertFalse(decision.may_execute)
        self.assertIn("Juan remains a stub", decision.reason)

    def test_disabled_travel_preserves_brain_fallback_without_invoking_juan(self):
        context = self._context()
        with patch.object(orchestration, "delegate_routed_travel_with_memory") as execute:
            result = orchestration.execute_routed_specialist_if_ready(
                context, "Travel", "Plan a trip to Costa Rica."
            )
            workflow_result = handle_request("Plan a trip to Costa Rica.", Mock(return_value="Fallback answer."))

        self.assertIsNone(result)
        execute.assert_not_called()
        self.assertIn("future specialist", workflow_result["response"])
        self.assertIn("Fallback answer.", workflow_result["response"])

    def test_existing_trip_calendar_route_never_enters_specialist_execution(self):
        question = "When is my Dominican Republic trip?"
        route = route_request(question)
        self.assertEqual(route.agent, "Sheila")
        decision = orchestration.specialist_execution_decision(route.agent)
        self.assertFalse(decision.is_specialist_candidate)
        self.assertIsNone(orchestration.execute_routed_specialist_if_ready(self._context(), route.agent, question))

    def test_richard_remains_unexecuted(self):
        route = route_request("What tax deductions should I consider?")
        decision = orchestration.specialist_execution_decision(route.agent)
        with patch.object(orchestration, "delegate_routed_travel_with_memory") as execute:
            result = orchestration.execute_routed_specialist_if_ready(self._context(), route.agent, "What tax deductions should I consider?")

        self.assertEqual(route.agent, "Richard")
        self.assertTrue(decision.is_specialist_candidate)
        self.assertFalse(decision.may_execute)
        self.assertIsNone(result)
        execute.assert_not_called()

    def test_ordinary_sheila_request_stays_on_normal_brain_path(self):
        with patch.object(orchestration, "retrieve_relevant_memory") as retrieve, \
                patch.object(orchestration, "delegate_routed_travel_with_memory") as execute:
            result = handle_request("Help me prepare for tomorrow", Mock(return_value="Prepared."))

        self.assertEqual(result["route"]["agent"], "Sheila")
        self.assertEqual(result["response"], "Prepared.")
        retrieve.assert_not_called()
        execute.assert_not_called()

    def test_enabled_travel_uses_stage_seven_helper_without_context_leakage(self):
        context = self._context()
        result = ExecutionResult("request-1", "succeeded", {"boundary_reached": True}, "center")
        with patch.object(orchestration, "TRAVEL_SPECIALIST_EXECUTION_ENABLED", True), \
                patch.object(orchestration, "delegate_routed_travel_with_memory", return_value=result) as execute:
            actual = orchestration.execute_routed_specialist_if_ready(
                context, "Travel", "Plan a trip to Costa Rica."
            )

        self.assertIs(actual, result)
        execute.assert_called_once_with(
            context,
            "Travel",
            "Plan a trip to Costa Rica.",
            constraints=(),
            authority_scope="read",
        )

    def test_enabled_travel_passes_only_stage_seven_selected_memory_to_center(self):
        context = self._context()
        with patch.object(orchestration, "TRAVEL_SPECIALIST_EXECUTION_ENABLED", True), \
                patch.object(orchestration, "retrieve_relevant_memory", return_value=[{
                    "id": "m-1", "category": "travel", "content": "Sam prefers nonstop flights.",
                    "metadata": {"private": True},
                }]), \
                patch.object(orchestration, "delegate_routed_specialist_with_center", return_value=ExecutionResult("request-1", "succeeded", authoritative_source="center")) as delegate:
            orchestration.execute_routed_specialist_if_ready(context, "Travel", "Plan a trip to Costa Rica.")

        relevant_context = delegate.call_args.kwargs["relevant_context"]
        self.assertEqual(relevant_context, {"memory_context": [{
            "category": "travel", "content": "Sam prefers nonstop flights.",
        }]})
        serialized = repr(relevant_context)
        self.assertNotIn("conversation_history", serialized)
        self.assertNotIn("user_profile", serialized)
        self.assertNotIn("oauth_token", serialized)
        self.assertNotIn("metadata", serialized)


if __name__ == "__main__":
    unittest.main()
