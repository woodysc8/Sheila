import unittest
from unittest.mock import patch
import brain


class BrainFallbackTests(unittest.TestCase):
    def test_think_returns_fallback_when_no_backend_configured(self):
        with patch.object(brain, "_try_gemini", return_value=None), \
             patch.object(brain, "_try_claude", return_value=None), \
             patch.object(brain, "_try_perplexity", return_value=None), \
             patch.object(brain.config, "GEMINI_API_KEYS", []), \
             patch.object(brain.config, "ANTHROPIC_API_KEY", "PUT_YOUR_ANTHROPIC_KEY_HERE"), \
             patch.object(brain.config, "PERPLEXITY_API_KEY", "PUT_YOUR_PERPLEXITY_KEY_HERE"):
            reply = brain.think("hello")
            self.assertIn("offline mode", reply.lower())


if __name__ == "__main__":
    unittest.main()
