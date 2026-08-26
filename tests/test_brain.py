import unittest
from unittest.mock import Mock, patch

import brain


class BrainOpenAITests(unittest.TestCase):
    def test_think_uses_openai_responses_with_retrieved_context(self):
        response = Mock()
        response.json.return_value = {"output_text": "Your calendar is clear."}
        with patch.object(brain.config, "OPENAI_API_KEY", "test-key"), \
             patch.object(brain.memory, "get_context", return_value="Memory"), \
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
             patch.object(brain.memory, "get_pending_notifications", return_value=[]), \
             patch("brain.requests.post", side_effect=brain.requests.RequestException("down")):
            reply = brain.think("hello")
        self.assertEqual(reply, brain.OPENAI_REQUEST_FAILED)
        self.assertFalse(hasattr(brain, "_try_gemini"))
        self.assertFalse(hasattr(brain, "_try_claude"))
        self.assertFalse(hasattr(brain, "_try_perplexity"))


if __name__ == "__main__":
    unittest.main()
