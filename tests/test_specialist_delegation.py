import unittest
from unittest.mock import patch

from agents.router import route_request
import orchestration


class StructuredSpecialistDelegationTests(unittest.TestCase):
    def _context(self):
        return orchestration.request_context(
            "Find flight options for November.",
            action="specialist_delegation",
            target="center",
            conversation_context={
                "conversation_history": ["private prior exchange"],
                "user_profile": {"name": "Sam"},
                "sam2_facts": ["private durable fact"],
            },
            relevant_memory=[{"content": "private memory"}],
        )

    def test_travel_route_uses_juan_structured_contract_without_internal_state(self):
        route = route_request("Find the best flight options for my November trip.")
        center_result = {
            "request_id": "request-1",
            "status": "succeeded",
            "authoritative_source": "specialist_boundary:juan_whey",
            "result": {"boundary_reached": True, "external_action_performed": False},
            "side_effects": [],
            "errors": [],
            "durable_memory_candidate": None,
        }
        context = self._context()
        with patch.object(orchestration.center_adapter, "submit_execution_task", return_value=center_result) as submit:
            result = orchestration.delegate_routed_specialist_with_center(
                context,
                route.agent,
                "Find the best flight options for my November trip.",
                relevant_context={"trip_month": "November"},
                constraints=("Avoid red-eyes", "Minimize cash"),
            )

        self.assertEqual(route.agent, "Travel")
        self.assertEqual(result.status, "succeeded")
        self.assertFalse(result.result["result"]["external_action_performed"])
        task = submit.call_args.args[0]
        self.assertEqual(task["raw_input"], "Find the best flight options for my November trip.")
        self.assertEqual(task["context"], {"trip_month": "November"})
        self.assertEqual(task["metadata"], {
            "request_id": context.request_id,
            "specialist": "juan_whey",
            "authority_scope": "read",
            "constraints": ["Avoid red-eyes", "Minimize cash"],
        })
        serialized = repr(task)
        self.assertNotIn("conversation_history", serialized)
        self.assertNotIn("user_profile", serialized)
        self.assertNotIn("sam2_facts", serialized)
        self.assertNotIn("private memory", serialized)

    def test_richard_route_uses_richard_structured_contract(self):
        route = route_request("What tax deductions should I consider?")
        context = self._context()
        with patch.object(orchestration.center_adapter, "submit_execution_task", return_value={
            "status": "succeeded",
            "result": {"boundary_reached": True, "external_action_performed": False},
        }) as submit:
            result = orchestration.delegate_routed_specialist_with_center(
                context, route.agent, "What tax deductions should I consider?"
            )

        self.assertEqual(route.agent, "Richard")
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(submit.call_args.args[0]["metadata"]["specialist"], "richard")
        self.assertEqual(submit.call_args.args[0]["context"], {})

    def test_non_specialist_route_does_not_call_center(self):
        context = self._context()
        with patch.object(orchestration.center_adapter, "submit_execution_task") as submit:
            result = orchestration.delegate_routed_specialist_with_center(
                context, "Sheila", "Help me prepare for tomorrow"
            )

        self.assertEqual(result.status, "failed")
        self.assertIn("No structured Center specialist", result.errors[0])
        submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
