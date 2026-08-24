import unittest
from unittest.mock import Mock

from agents.router import route_request
from agents.workflow import handle_request


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

    def test_planned_specialist_is_not_invoked(self):
        sheila_brain = Mock(return_value="Here is a careful general answer.")
        result = handle_request("Build me a travel itinerary", sheila_brain)

        sheila_brain.assert_called_once_with("Build me a travel itinerary")
        self.assertEqual(result["route"]["agent"], "Travel")
        self.assertIn("future specialist", result["response"])
        self.assertIn("I'll handle it myself", result["response"])


if __name__ == "__main__":
    unittest.main()
