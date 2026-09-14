import inspect
import unittest
from unittest.mock import Mock, patch

import requests

import orchestration
import sam2_memory_client


class Sam2MemoryClientTests(unittest.TestCase):
    def setUp(self):
        self.settings = patch.multiple(
            sam2_memory_client.config,
            SECOND_BRAIN_URL="https://sam2.example/",
            SECOND_BRAIN_SERVICE_TOKEN="service-token",
            SECOND_BRAIN_USER_ID="sam-user",
            SECOND_BRAIN_TIMEOUT=4.5,
        )
        self.settings.start()

    def tearDown(self):
        self.settings.stop()

    @staticmethod
    def _response(body):
        response = Mock()
        response.content = b"response"
        response.json.return_value = body
        response.raise_for_status.return_value = None
        return response

    def test_successful_search_uses_configured_service_identity_and_payload(self):
        response = self._response({"memories": [{"id": "m-1", "content": "Sam prefers Hyatt."}]})
        with patch.object(sam2_memory_client.requests, "post", return_value=response) as post:
            result = sam2_memory_client.Sam2MemoryClient().search(
                "travel preferences",
                category="travel",
                source="user",
                memory_key="hotel_preference",
                sort="updated_at",
                limit=7,
            )

        self.assertEqual(result, [{"id": "m-1", "content": "Sam prefers Hyatt."}])
        self.assertEqual(post.call_args.args[0], "https://sam2.example/api/memories/search")
        self.assertEqual(post.call_args.kwargs["headers"], {
            "Authorization": "Bearer service-token",
            "X-Second-Brain-User": "sam-user",
        })
        self.assertEqual(post.call_args.kwargs["json"], {
            "query": "travel preferences",
            "category": "travel",
            "source": "user",
            "memory_key": "hotel_preference",
            "sort": "updated_at",
            "limit": 7,
        })
        self.assertEqual(post.call_args.kwargs["timeout"], 4.5)
        self.assertNotIn("service-token", repr(result))

    def test_empty_memories_are_a_successful_result(self):
        with patch.object(sam2_memory_client.requests, "post", return_value=self._response({"memories": []})):
            self.assertEqual(sam2_memory_client.Sam2MemoryClient().search("travel preferences"), [])

    def test_network_and_invalid_response_fail_clearly(self):
        with patch.object(sam2_memory_client.requests, "post", side_effect=requests.Timeout("down")):
            with self.assertRaisesRegex(sam2_memory_client.Sam2MemorySearchError, "could not be reached"):
                sam2_memory_client.Sam2MemoryClient().search("travel")

        with patch.object(sam2_memory_client.requests, "post", return_value=self._response({"memories": "wrong"})):
            with self.assertRaisesRegex(sam2_memory_client.Sam2MemorySearchError, "invalid memories payload"):
                sam2_memory_client.Sam2MemoryClient().search("travel")

    def test_authentication_and_server_failures_are_distinct(self):
        for status_code, error_type in (
            (401, sam2_memory_client.Sam2MemoryAuthenticationError),
            (503, sam2_memory_client.Sam2MemoryServerError),
        ):
            response = self._response({})
            response.raise_for_status.side_effect = requests.HTTPError(response=Mock(status_code=status_code))
            with self.subTest(status_code=status_code), \
                    patch.object(sam2_memory_client.requests, "post", return_value=response):
                with self.assertRaises(error_type):
                    sam2_memory_client.Sam2MemoryClient().search("travel")

    def test_missing_configuration_fails_before_request(self):
        with patch.object(sam2_memory_client.config, "SECOND_BRAIN_SERVICE_TOKEN", ""), \
                patch.object(sam2_memory_client.requests, "post") as post:
            with self.assertRaises(sam2_memory_client.Sam2MemoryConfigurationError):
                sam2_memory_client.Sam2MemoryClient().search("travel")
        post.assert_not_called()

    def test_configured_user_id_cannot_be_overridden(self):
        client = sam2_memory_client.Sam2MemoryClient()
        self.assertNotIn("user_id", inspect.signature(client.search).parameters)
        with self.assertRaises(TypeError):
            client.search("travel", user_id="another-user")

    def test_orchestration_retrieval_is_explicit_and_does_not_persist_or_delegate(self):
        client = Mock()
        client.search.return_value = [{"id": "m-1", "content": "Sam prefers direct flights."}]
        with patch.object(orchestration, "Sam2MemoryClient", return_value=client), \
                patch.object(orchestration, "persist_memory_candidate") as persist, \
                patch.object(orchestration.center_adapter, "submit_execution_task") as submit:
            result = orchestration.retrieve_relevant_memory(query="travel preferences", category="travel", limit=3)

        self.assertEqual(result, [{"id": "m-1", "content": "Sam prefers direct flights."}])
        client.search.assert_called_once_with(
            "travel preferences", category="travel", source=None,
            memory_key=None, sort="relevance", limit=3,
        )
        persist.assert_not_called()
        submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
