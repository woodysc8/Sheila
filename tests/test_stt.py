import unittest
from unittest.mock import patch
import stt


class SttFallbackTests(unittest.TestCase):
    def test_record_while_held_falls_back_to_text_input_when_no_microphone(self):
        with patch.object(stt, "sd", None), patch("builtins.input", return_value="hello there"):
            self.assertEqual(stt.record_while_held(), "hello there")


if __name__ == "__main__":
    unittest.main()
