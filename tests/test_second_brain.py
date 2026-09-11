import unittest
from unittest.mock import Mock, call, patch

import config
import memory
import second_brain


class SecondBrainClientTests(unittest.TestCase):
    def test_remember_serializes_memory_key_and_metadata(self):
        response = Mock(content=b'{"id":"memory-1"}')
        response.json.return_value = {"id": "memory-1"}
        with patch.object(second_brain.config, "SECOND_BRAIN_USER_ID", "sam-user"), \
            patch.object(second_brain.requests, "request", return_value=response) as request:
            client = second_brain.SecondBrainClient("https://sam.example", "token", 3)
            result = client.remember(
                "preference", "Sam prefers Hyatt.", "user", importance=5,
                metadata={"memory_key": "hotel", "explicit": True},
            )

        self.assertEqual(result["id"], "memory-1")
        self.assertEqual(request.call_args.args[:2], ("POST", "https://sam.example/api/memories"))
        self.assertEqual(request.call_args.kwargs["headers"]["Authorization"], "Bearer token")
        self.assertEqual(request.call_args.kwargs["headers"]["X-Second-Brain-User"], "sam-user")
        self.assertEqual(request.call_args.kwargs["json"]["memory_key"], "hotel")

    def test_recall_update_and_forget_use_expected_endpoints(self):
        response = Mock(content=b'{"memories":[]}')
        response.json.return_value = {"memories": []}
        with patch.object(second_brain.requests, "request", return_value=response) as request:
            client = second_brain.SecondBrainClient("https://sam.example", "token")
            self.assertEqual(client.recall("hotel", "preference", 4, "user"), [])
            client.update("memory-1", content="updated")
            self.assertTrue(client.forget("memory-1"))

        self.assertEqual([call.args[1] for call in request.call_args_list], [
            "https://sam.example/api/memories/search",
            "https://sam.example/api/memories/memory-1",
            "https://sam.example/api/memories/memory-1",
        ])


class SheilaMemoryProviderTests(unittest.TestCase):
    def test_second_brain_provider_is_selected_for_memory_crud(self):
        client = Mock()
        client.remember.return_value = {"id": "remote-1"}
        client.recall.return_value = [{"id": "remote-1"}]
        client.update.return_value = {"id": "remote-1", "content": "updated"}
        client.forget.return_value = True
        with patch.object(config, "SHEILA_MEMORY_BACKEND", "second_brain"), \
                patch.object(memory.second_brain, "SecondBrainClient", return_value=client):
            self.assertEqual(
                memory.remember("preference", "Sam prefers Hyatt.", "user", importance=5),
                {"id": "remote-1"},
            )
            self.assertEqual(memory.recall("hotel"), [{"id": "remote-1"}])
            self.assertEqual(memory.update("remote-1", content="updated"), {"id": "remote-1", "content": "updated"})
            self.assertTrue(memory.forget("remote-1"))
        client.remember.assert_called_once_with("preference", "Sam prefers Hyatt.", "user", None, 5, None)
        client.recall.assert_called_once_with("hotel", None, 10)
        client.update.assert_called_once_with("remote-1", content="updated")
        client.forget.assert_called_once_with("remote-1")

    def test_remote_failure_falls_back_to_local(self):
        with patch.object(config, "SHEILA_MEMORY_BACKEND", "second_brain"), \
                patch.object(config, "SHEILA_MEMORY_FALLBACK", True), \
                patch.object(memory.second_brain.SecondBrainClient, "recall", side_effect=second_brain.SecondBrainError("down")), \
                patch.object(memory, "_local_recall", return_value=[{"id": "local-1"}]) as local:
            self.assertEqual(memory.recall("hotel"), [{"id": "local-1"}])
        local.assert_called_once_with("hotel", None, 10)

    def test_fallback_is_observable(self):
        with patch.object(config, "SHEILA_MEMORY_BACKEND", "second_brain"), \
                patch.object(config, "SHEILA_MEMORY_FALLBACK", True), \
                patch.object(memory.second_brain.SecondBrainClient, "recall", side_effect=second_brain.SecondBrainError("down")), \
                patch.object(memory, "_local_recall", return_value=[]) as local, \
                patch.object(memory.logger, "warning") as warning:
            memory.recall("hotel")
        local.assert_called_once_with("hotel", None, 10)
        warning.assert_called_once()

    def test_forget_latest_requests_updated_at_ordering(self):
        client = Mock()
        client.recall.return_value = [{"id": "latest"}]
        with patch.object(config, "SHEILA_MEMORY_BACKEND", "second_brain"), \
                patch.object(memory.second_brain, "SecondBrainClient", return_value=client), \
                patch.object(memory, "forget", return_value=True) as forget:
            self.assertTrue(memory.forget_latest_structured(source="user"))
        client.recall.assert_called_once_with(limit=1, source="user", sort="updated_at")
        forget.assert_called_once_with("latest")


if __name__ == "__main__":
    unittest.main()
