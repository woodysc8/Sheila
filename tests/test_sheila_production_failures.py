import unittest
from unittest.mock import patch

import sheila_handler


class SheilaProductionPathRegressionTests(unittest.TestCase):
    def test_what_does_sam_2_know_about_me_reads_durable_memory(self):
        with patch.object(sheila_handler.memory, "recall", return_value=[
            {"content": "Sam prefers direct flights."},
        ]) as recall, patch.object(sheila_handler, "handle_request") as workflow, \
                patch.object(sheila_handler.memory, "log_exchange"):
            reply = sheila_handler.process_message("What does Sam 2 know about me?")

        recall.assert_called_once_with(limit=10)
        workflow.assert_not_called()
        self.assertEqual(reply, "Sam 2 knows:\n- Sam prefers direct flights.")

    def test_remind_me_tomorrow_at_11am_uses_existing_reminder_path(self):
        text = "Remind me tomorrow at 11am to go to the post office thing"
        with patch("agents.workflow.sheila_tasks.operational_store.require_configured"), \
                patch("agents.workflow.sheila_tasks.operational_store.configured", return_value=True), \
                patch("agents.workflow.sheila_tasks.operational_store.list_reminders", return_value=[]), \
                patch("agents.workflow.sheila_tasks.operational_store.create_reminder") as create, \
                patch.object(sheila_handler.memory, "log_exchange"):
            reply = sheila_handler.process_message(text)

        self.assertEqual(create.call_args.args[0], "go to the post office thing")
        self.assertEqual(create.call_args.args[1].hour, 11)
        self.assertEqual(reply, "Reminder set: go to the post office thing.")

    def test_next_month_events_excluding_work_calls_stays_on_personal_calendar_path(self):
        text = "What events do i have coming up over the next month? Exclude work calls"
        with patch.object(sheila_handler.personal_calendar, "handle_personal_calendar_request", return_value="Personal calendar:\n- Dentist") as calendar, \
                patch.object(sheila_handler.brain, "think") as brain, \
                patch.object(sheila_handler.memory, "log_exchange"):
            reply = sheila_handler.process_message(text)

        calendar.assert_called_once_with(text)
        brain.assert_not_called()
        self.assertEqual(reply, "Personal calendar:\n- Dentist")

    def test_good_morning_uses_the_existing_morning_briefing(self):
        with patch.object(sheila_handler.briefing, "build_morning_briefing", return_value="Morning briefing") as briefing, \
                patch.object(sheila_handler, "handle_request") as workflow, \
                patch.object(sheila_handler.memory, "log_exchange"):
            reply = sheila_handler.process_message("Good morning")

        briefing.assert_called_once_with()
        workflow.assert_not_called()
        self.assertEqual(reply, "Morning briefing")
