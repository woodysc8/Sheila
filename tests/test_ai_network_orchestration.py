from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import orchestration
from orchestration_contract import ExecutionResult
import sheila_handler


ZACH = "Add Zach Bryan at Gillette Stadium October 2nd and remember that I'm going."


class AiNetworkOrchestrationTests(unittest.TestCase):
    def test_calendar_success_and_memory_success_are_separate_authorities(self):
        with patch.object(sheila_handler, "handle_request", return_value={"response": "Added Zach Bryan at Gillette Stadium to your personal calendar."}), \
             patch.object(sheila_handler.orchestration, "persist_memory_candidate", return_value=ExecutionResult("r", "succeeded", {"id": "sam2-1"}, "sam2")) as persist, \
             patch.object(sheila_handler.memory, "log_exchange"):
            reply = sheila_handler.process_message(ZACH)
        self.assertIn("personal calendar", reply)
        self.assertIn("Sam 2", reply)
        candidate = persist.call_args.args[1]
        self.assertIn("Zach Bryan", candidate["content"])
        self.assertNotIn("event_id", candidate)
        self.assertNotIn("start", candidate)

    def test_calendar_failure_does_not_create_durable_memory_proof(self):
        with patch.object(sheila_handler, "handle_request", return_value={"response": "I couldn't add Zach Bryan."}), \
             patch.object(sheila_handler.orchestration, "persist_memory_candidate") as persist, \
             patch.object(sheila_handler.memory, "log_exchange"):
            reply = sheila_handler.process_message(ZACH)
        self.assertIn("couldn't", reply)
        persist.assert_not_called()

    def test_calendar_success_memory_failure_is_reported_as_partial(self):
        with patch.object(sheila_handler, "handle_request", return_value={"response": "Added Zach Bryan at Gillette Stadium to your personal calendar."}), \
             patch.object(sheila_handler.orchestration, "persist_memory_candidate", return_value=ExecutionResult("r", "failed", authoritative_source="sam2", errors=("down",))), \
             patch.object(sheila_handler.memory, "log_exchange"):
            reply = sheila_handler.process_message(ZACH)
        self.assertIn("calendar result is confirmed", reply)
        self.assertIn("couldn't save", reply)

    def test_combined_calendar_and_memory_read_labels_authorities(self):
        with patch.object(sheila_handler.personal_calendar, "handle_personal_calendar_request", return_value="Personal calendar:\n- Dentist"), \
             patch.object(sheila_handler.memory, "recall", return_value=[{"content": "I said tomorrow is busy."}]), \
             patch.object(sheila_handler.memory, "log_exchange"):
            reply = sheila_handler.process_message("What's on my calendar tomorrow, and remind me what I said about tomorrow?")
        self.assertIn("Calendar (Google Calendar)", reply)
        self.assertIn("Remembered context (Sam 2)", reply)
        self.assertIn("Dentist", reply)

    def test_center_calendar_read_uses_structured_execution_request(self):
        with patch.object(orchestration.center_adapter, "submit_execution_task", return_value={"status": "done", "authoritative_source": "google_calendar", "result": {"events": [{"title": "Dentist"}], "count": 1}}) as submit, \
             patch.object(sheila_handler.memory, "log_exchange"):
            reply = sheila_handler.process_message("Check my calendar for tomorrow.")
        self.assertIn("Dentist", reply)
        task = submit.call_args.args[0]
        self.assertEqual(task["metadata"]["capability"], "calendar_read")
        self.assertEqual(task["metadata"]["authority_scope"], "read")
        self.assertTrue(callable(task["metadata"]["calendar_reader"]))
        self.assertNotIn("memory", task["context"])

    def test_center_failure_is_not_reported_as_calendar_success(self):
        with patch.object(orchestration.center_adapter, "submit_execution_task", side_effect=orchestration.center_adapter.CenterUnavailableError("down")), \
             patch.object(sheila_handler.memory, "log_exchange"):
            reply = sheila_handler.process_message("Check my calendar for tomorrow.")
        self.assertIn("could not complete", reply)
        self.assertNotIn("added", reply.lower())

    def test_calendar_read_vertical_slice_uses_real_center_with_injected_sheila_reader(self):
        """Exercise Sheila -> Center -> injected reader without Google access."""
        center_path = Path(__file__).resolve().parents[2] / "Center"
        reader = Mock(return_value=[{"id": "google-1", "title": "Dentist"}])
        with patch.object(sheila_handler.personal_calendar, "get_personal_calendar_events", reader), \
             patch.object(orchestration.center_adapter.config, "SHEILA_CENTER_PATH", str(center_path)), \
             patch.object(sheila_handler.memory, "log_exchange"):
            reply = sheila_handler.process_message("Check my calendar for tomorrow.")

        self.assertIn("Dentist", reply)
        reader.assert_called_once()

    def test_center_empty_calendar_result_is_formatted_naturally(self):
        with patch.object(orchestration.center_adapter, "submit_execution_task", return_value={
            "status": "succeeded",
            "authoritative_source": "google_calendar",
            "result": {"events": [], "count": 0},
        }), patch.object(sheila_handler.memory, "log_exchange"):
            reply = sheila_handler.process_message("Check my calendar for tomorrow.")

        self.assertEqual(reply, "Your Google Calendar is clear tomorrow.")


if __name__ == "__main__":
    unittest.main()
