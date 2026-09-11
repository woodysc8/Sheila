import inspect
import unittest
from unittest.mock import Mock, patch

import center_adapter
import memory


class CenterAdapterTests(unittest.TestCase):
    def test_submits_structured_task_to_center(self):
        handler = Mock(return_value={"status": "done"})
        task = {
            "raw_input": "Research the market outlook",
            "context": {"project": "briefing"},
            "metadata": {"request_id": "req-1"},
        }

        with patch.object(center_adapter, "_load_handler", return_value=handler), \
             patch.object(center_adapter.config, "SHEILA_CENTER_PATH", "."):
            result = center_adapter.submit_execution_task(task)

        self.assertEqual(result, {"status": "done"})
        handler.assert_called_once_with(
            "Research the market outlook",
            context={"project": "briefing"},
            metadata={"request_id": "req-1"},
        )

    def test_returns_center_result_unchanged(self):
        result_from_center = {
            "task_id": "center-1", "owner": "richard", "result": {"answer": "done"}, "status": "done"
        }
        handler = Mock(return_value=result_from_center)

        with patch.object(center_adapter, "_load_handler", return_value=handler), \
             patch.object(center_adapter.config, "SHEILA_CENTER_PATH", "."):
            result = center_adapter.submit_execution_task({"raw_input": "Need tax advice"})

        self.assertIs(result, result_from_center)

    def test_center_failure_becomes_clear_sheila_error(self):
        handler = Mock(side_effect=RuntimeError("Center database unavailable"))

        with patch.object(center_adapter, "_load_handler", return_value=handler), \
             patch.object(center_adapter.config, "SHEILA_CENTER_PATH", "."):
            with self.assertRaisesRegex(center_adapter.CenterUnavailableError, "Center could not execute"):
                center_adapter.submit_execution_task({"raw_input": "Plan a trip"})

    def test_missing_center_configuration_is_clear(self):
        with patch.object(center_adapter.config, "SHEILA_CENTER_PATH", ""):
            with self.assertRaisesRegex(center_adapter.CenterConfigurationError, "SHEILA_CENTER_PATH"):
                center_adapter.submit_execution_task({"raw_input": "Plan a trip"})

    def test_memory_layer_has_no_center_dependency(self):
        source = inspect.getsource(memory).lower()
        self.assertNotIn("center_adapter", source)
        self.assertNotIn("submit_execution_task", source)

    def test_adapter_does_not_introduce_sam_2_dependency_into_center_boundary(self):
        source = inspect.getsource(center_adapter).lower()
        self.assertNotIn("second_brain", source)
        self.assertNotIn("sam 2", source)


if __name__ == "__main__":
    unittest.main()
