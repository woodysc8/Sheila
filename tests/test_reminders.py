import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import reminders


class ReminderTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(reminders.config, "DB_PATH", os.path.join(self.temp_dir.name, "memory.db"))
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_reminder_persists_timezone_aware_fields_and_candidate_evaluation(self):
        eastern = ZoneInfo("America/New_York")
        reminder = reminders.create(
            "Complete doctor paperwork",
            datetime(2026, 8, 27, 17, tzinfo=eastern),
            remind_before=timedelta(hours=2),
        )
        self.assertEqual(reminders.pending()[0], reminder)
        now = datetime(2026, 8, 27, 15, tzinfo=eastern)
        self.assertEqual(reminders.due_candidates(now)[0].text, "Complete doctor paperwork")
        self.assertEqual(reminders.due_candidates(now, lambda _start, _due: True), [])

    def test_naive_reminder_time_is_rejected(self):
        with self.assertRaises(ValueError):
            reminders.create("No timezone", datetime(2026, 8, 27, 9))

    def test_status_can_be_cancelled_without_sending(self):
        reminder = reminders.create("Cancel this", datetime.now(ZoneInfo("America/New_York")) + timedelta(days=1))
        reminders.set_status(reminder.id, "cancelled")
        self.assertEqual(reminders.pending(), [])


if __name__ == "__main__":
    unittest.main()
