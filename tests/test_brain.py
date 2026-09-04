import unittest
from unittest.mock import Mock, patch

import brain


class BrainOpenAITests(unittest.TestCase):
    def test_memory_context_includes_structured_and_document_knowledge(self):
        facade = brain.Brain()
        with patch.object(brain.memory, "get_context", return_value="Recent conversation"), \
             patch.object(brain.memory, "get_structured_context", return_value="- [preference] Boston"), \
             patch.object(brain.knowledge, "search_documents", return_value="[from User Background]: College of the Holy Cross, Worcester, MA May 2026") as search:
            context = facade.get_memory_context("What college did I attend?")

        self.assertIn("[STRUCTURED MEMORY]", context)
        self.assertIn("Boston", context)
        self.assertIn("[DOCUMENT KNOWLEDGE]", context)
        self.assertIn("User Background", context)
        self.assertIn("College of the Holy Cross", context)
        search.assert_called_once_with("What college did I attend?", top_k=4)

    def test_document_retrieval_failure_keeps_memory_context(self):
        facade = brain.Brain()
        with patch.object(brain.memory, "get_context", return_value="Recent conversation"), \
             patch.object(brain.memory, "get_structured_context", return_value="Structured fact"), \
             patch.object(brain.knowledge, "search_documents", side_effect=RuntimeError("embedding unavailable")):
            context = facade.get_memory_context("What college did I attend?")

        self.assertIn("Recent conversation", context)
        self.assertIn("Structured fact", context)
        self.assertNotIn("[DOCUMENT KNOWLEDGE]", context)

    def test_empty_document_retrieval_keeps_memory_context(self):
        facade = brain.Brain()
        with patch.object(brain.memory, "get_context", return_value="Recent conversation"), \
             patch.object(brain.memory, "get_structured_context", return_value="Structured fact"), \
             patch.object(brain.knowledge, "search_documents", return_value="") as search:
            context = facade.get_memory_context("What college did I attend?")

        self.assertIn("Recent conversation", context)
        self.assertIn("Structured fact", context)
        self.assertNotIn("[DOCUMENT KNOWLEDGE]", context)
        search.assert_called_once_with("What college did I attend?", top_k=4)

    def test_brain_facade_delegates_to_memory_and_knowledge_backends(self):
        facade = brain.Brain()
        memory_item = {"id": 1, "content": "A fact"}
        with patch.object(brain.memory, "remember", return_value=memory_item) as remember, \
             patch.object(brain.memory, "recall", return_value=[memory_item]) as recall, \
             patch.object(brain.memory, "update", return_value=memory_item) as update, \
             patch.object(brain.memory, "forget", return_value=True) as forget, \
             patch.object(brain.knowledge, "search_documents", return_value="document") as search, \
             patch.object(brain.knowledge, "ingest_document") as ingest:
            self.assertEqual(facade.remember("fact", "A fact", "manual"), memory_item)
            self.assertEqual(facade.recall("fact"), [memory_item])
            self.assertEqual(facade.update(1, content="Updated"), memory_item)
            self.assertTrue(facade.forget(1))
            self.assertEqual(facade.search_documents("question"), "document")
            facade.ingest_document("doc-1", "text", "upload.txt")

        remember.assert_called_once_with("fact", "A fact", "manual", None, 0, None)
        recall.assert_called_once_with("fact", None, 10)
        update.assert_called_once_with(1, content="Updated")
        forget.assert_called_once_with(1)
        search.assert_called_once_with("question", top_k=4)
        ingest.assert_called_once_with("doc-1", "text", "upload.txt")

    def test_think_uses_openai_responses_with_retrieved_context(self):
        response = Mock()
        response.json.return_value = {"output_text": "Your calendar is clear."}
        with patch.object(brain.config, "OPENAI_API_KEY", "test-key"), \
             patch.object(brain.memory, "get_context", return_value="Memory"), \
               patch.object(brain.memory, "get_structured_context", return_value="Structured memory"), \
               patch.object(brain.knowledge, "search_documents", return_value=""), \
             patch.object(brain.memory, "get_pending_notifications", return_value=[]), \
             patch("brain.requests.post", return_value=response) as post:
            reply = brain.think("What is on my calendar?", context="Calendar results: none")

        self.assertEqual(reply, "Your calendar is clear.")
        self.assertEqual(post.call_args.args[0], brain.OPENAI_RESPONSES_URL)
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(post.call_args.kwargs["json"]["model"], brain.config.OPENAI_MODEL)
        self.assertIn("Calendar results: none", post.call_args.kwargs["json"]["input"])
        self.assertIn("calendar-only questions", post.call_args.kwargs["json"]["input"])
        self.assertIn("never claim zero results", post.call_args.kwargs["json"]["input"])
        self.assertIn("source of truth", post.call_args.kwargs["json"]["input"])
        self.assertIn("does not prove a company is a client", post.call_args.kwargs["json"]["input"])

    def test_missing_openai_configuration_does_not_make_a_provider_call(self):
        with patch.object(brain.config, "OPENAI_API_KEY", ""), patch("brain.requests.post") as post:
            reply = brain.think("hello")
        self.assertEqual(reply, brain.OPENAI_NOT_CONFIGURED)
        post.assert_not_called()

    def test_openai_failure_has_no_other_provider_fallback(self):
        with patch.object(brain.config, "OPENAI_API_KEY", "test-key"), \
             patch.object(brain.memory, "get_context", return_value="Memory"), \
               patch.object(brain.memory, "get_structured_context", return_value="Structured memory"), \
               patch.object(brain.knowledge, "search_documents", return_value=""), \
             patch.object(brain.memory, "get_pending_notifications", return_value=[]), \
             patch("brain.requests.post", side_effect=brain.requests.RequestException("down")):
            reply = brain.think("hello")
        self.assertEqual(reply, brain.OPENAI_REQUEST_FAILED)
        self.assertFalse(hasattr(brain, "_try_gemini"))
        self.assertFalse(hasattr(brain, "_try_claude"))
        self.assertFalse(hasattr(brain, "_try_perplexity"))


if __name__ == "__main__":
    unittest.main()
