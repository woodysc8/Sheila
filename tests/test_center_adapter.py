import inspect
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import center_adapter
import memory


class CenterAdapterTests(unittest.TestCase):
    def test_center_runtime_dependency_is_pinned_and_secret_free(self):
        requirements = (Path(__file__).resolve().parents[1] /
                        "requirements-center-runtime.txt").read_text(encoding="utf-8")
        self.assertIn(
            "center @ git+https://github.com/woodysc8/Center.git@1d81c89bd3d4a76c674d50d9bb4f79eb05a3cf66",
            requirements,
        )
        self.assertNotIn("@github.com:", requirements)
        self.assertNotIn("token", requirements.lower())

    def test_runtime_directory_loads_center_handler_and_restores_sheila_imports(self):
        center_path = Path(__file__).resolve().parents[2] / "Center"
        original_path = list(__import__("sys").path)
        original_config = __import__("sys").modules.get("config")

        with patch.object(center_adapter.config, "SHEILA_CENTER_PATH", str(center_path)):
            self.assertTrue(center_path.is_dir())
            with center_adapter._center_import_scope(center_path):
                handler = center_adapter._load_handler()
                self.assertTrue(callable(handler))

        self.assertEqual(__import__("sys").path, original_path)
        self.assertIs(__import__("sys").modules.get("config"), original_config)

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
