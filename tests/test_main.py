import unittest
from unittest.mock import patch

import main
import sheila_handler


class TextMainTests(unittest.TestCase):
    def test_process_message_uses_workflow_and_logs_exchange(self):
        with patch.object(sheila_handler, "handle_request", return_value={"response": "Hello."}) as workflow, \
             patch.object(sheila_handler.memory, "log_exchange") as log_exchange:
            reply = main.process_message("hello")
        self.assertEqual(reply, "Hello.")
        workflow.assert_called_once_with("hello", response_handler=sheila_handler.brain.think)
        log_exchange.assert_called_once_with("hello", "Hello.", important=False)

    def test_good_morning_invokes_fact_grounded_morning_protocol(self):
        with patch.object(sheila_handler.briefing, "build_morning_briefing", return_value="Calendar today:\n- 6 events") as briefing, \
             patch.object(sheila_handler, "handle_request") as workflow, \
             patch.object(sheila_handler.memory, "log_exchange") as log_exchange:
            reply = main.process_message("good morning Sheila")
        self.assertEqual(reply, "Calendar today:\n- 6 events")
        briefing.assert_called_once_with()
        workflow.assert_not_called()
        log_exchange.assert_called_once_with("good morning Sheila", reply, important=False)

    def test_hello_does_not_invoke_morning_protocol(self):
        with patch.object(sheila_handler.briefing, "build_morning_briefing") as briefing, \
             patch.object(sheila_handler, "handle_request", return_value={"response": "Hello."}), \
             patch.object(sheila_handler.memory, "log_exchange"):
            main.process_message("hello")
        briefing.assert_not_called()

    def test_remember_and_forget_keep_current_exchange_logged(self):
        with patch.object(sheila_handler, "handle_request", return_value={"response": "Done."}), \
             patch.object(sheila_handler.memory, "forget_last") as forget_last, \
             patch.object(sheila_handler.memory, "log_exchange") as log_exchange:
            main.process_message("Forget this, and remember this")
        forget_last.assert_called_once()
        log_exchange.assert_called_once_with("Forget this, and remember this", "Done.", important=True)

    def test_main_is_a_text_prompt_loop(self):
        with patch.object(main.memory, "init_db"), \
             patch("builtins.input", side_effect=["hello", "/quit"]) as text_input, \
             patch.object(main, "process_message", return_value="Hello.") as process, \
             patch("builtins.print") as output:
            main.main()
        text_input.assert_called_with("You: ")
        process.assert_called_once_with("hello")
        output.assert_any_call("Sheila: Hello.")
        self.assertNotIn("stt", main.__dict__)
        self.assertNotIn("tts", main.__dict__)

    def test_terminal_reexports_the_shared_message_handler(self):
        self.assertIs(main.process_message, sheila_handler.process_message)


if __name__ == "__main__":
    unittest.main()
