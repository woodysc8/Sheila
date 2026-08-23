import unittest
from unittest.mock import patch
import tts


class TtsFallbackTests(unittest.TestCase):
    def test_speak_uses_fallback_when_piper_is_unavailable(self):
        with patch.object(tts, "_piper_available", return_value=False), patch.object(tts, "speak_fallback") as mock_fallback:
            tts.speak("hello there")
            mock_fallback.assert_called_once_with("hello there")


if __name__ == "__main__":
    unittest.main()
