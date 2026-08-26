import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

import briefing
from integrations import asana
from integrations.google_auth import GoogleAuthError


NOW = datetime(2026, 8, 26, 9, tzinfo=timezone.utc)


class MorningBriefingTests(unittest.TestCase):
    def _brief(self, *, events=None, messages=None, brew=None, overdue=None, tasks=None, now=NOW):
        with patch.object(briefing.calendar, "get_events", return_value=events or []), \
             patch.object(briefing.gmail, "search_messages", side_effect=[messages or [], brew or []]), \
             patch.object(briefing.asana, "get_overdue_tasks", return_value=overdue or []), \
             patch.object(briefing.asana, "get_tasks", return_value=tasks or []), \
             patch.object(briefing, "get_weather_summary", return_value="Clear, 70°F now; high 80°F."), \
             patch.object(briefing.memory, "mark_notifications_delivered"):
            return briefing.build_morning_briefing(now)

    def test_calendar_is_authenticated_and_formats_all_day_timed_and_first_meeting(self):
        events = [
            {"title": "Press release", "start": "2026-08-26", "end": "2026-08-27", "location": ""},
            {"title": "Later", "start": "2026-08-26T15:00:00-04:00", "end": "2026-08-26T15:30:00-04:00", "location": ""},
            {"title": "Standup", "start": "2026-08-26T09:15:00-04:00", "end": "2026-08-26T09:45:00-04:00", "location": ""},
        ]
        summary = self._brief(events=events)
        self.assertIn("Calendar today:", summary)
        self.assertIn("- 3 event(s)", summary)
        self.assertIn("All day: Press release", summary)
        self.assertIn("9:15 AM–9:45 AM: Standup", summary)
        self.assertIn("First meeting: 9:15 AM — Standup.", summary)
        self.assertNotIn("CALENDAR_ICS_URL", briefing.__dict__)

    def test_calendar_zero_and_failure_are_distinct(self):
        self.assertIn("- 0 event(s)", self._brief())
        with patch.object(briefing.calendar, "get_events", side_effect=GoogleAuthError("down")), \
             patch.object(briefing.gmail, "search_messages", return_value=[]), \
             patch.object(briefing.asana, "get_overdue_tasks", return_value=[]), \
             patch.object(briefing.asana, "get_tasks", return_value=[]), \
             patch.object(briefing, "get_weather_summary", return_value="Clear."), \
             patch.object(briefing.memory, "mark_notifications_delivered"):
            summary = briefing.build_morning_briefing(NOW)
        self.assertIn("Google Calendar isn't available", summary)
        self.assertNotIn("- 0 event(s)", summary)

    def test_asana_deadline_sections_filter_completed_and_undated_tasks(self):
        tasks = [
            {"name": "Today", "due_on": "2026-08-26", "completed": False, "project": "Ops"},
            {"name": "Tomorrow", "due_on": "2026-08-27", "completed": False, "project": "Ops"},
            {"name": "Done", "due_on": "2026-08-26", "completed": True},
            {"name": "Undated", "due_on": None, "due_at": None, "completed": False},
        ]
        summary = self._brief(overdue=[{"name": "Late", "due_on": "2026-08-25", "completed": False, "project": "Client"}], tasks=tasks)
        self.assertIn("Overdue tasks: 1", summary)
        self.assertIn("Late (due 2026-08-25) — Client", summary)
        self.assertIn("Tasks due today: 1", summary)
        self.assertIn("Tasks due tomorrow: 1", summary)
        self.assertNotIn("Done", summary)
        self.assertNotIn("Undated", summary)

    def test_asana_zero_and_failure_are_distinct(self):
        summary = self._brief()
        self.assertIn("No overdue tasks found.", summary)
        self.assertIn("No tasks due today.", summary)
        with patch.object(briefing.calendar, "get_events", return_value=[]), \
             patch.object(briefing.gmail, "search_messages", return_value=[]), \
             patch.object(briefing.asana, "get_overdue_tasks", side_effect=asana.AsanaError("down")), \
             patch.object(briefing, "get_weather_summary", return_value="Clear."), \
             patch.object(briefing.memory, "mark_notifications_delivered"):
            summary = briefing.build_morning_briefing(NOW)
        self.assertIn("Unable to retrieve Asana right now.", summary)
        self.assertNotIn("No overdue tasks found.", summary)

    def test_gmail_queries_are_bounded_and_old_mail_is_not_current_morning_brew(self):
        old_brew = {"sender": "Morning Brew <crew@morningbrew.com>", "subject": "Morning Brew", "date": "Tue, 25 Aug 2026 08:00:00 +0000"}
        current = {"sender": "Client <client@example.com>", "subject": "Update", "date": "Wed, 26 Aug 2026 09:00:00 +0000"}
        with patch.object(briefing.gmail, "search_messages", return_value=[old_brew, current]) as search:
            briefing.get_todays_gmail_messages(NOW)
        self.assertIn("after:2026/08/26 before:2026/08/27", search.call_args.args[0])
        self.assertIsNone(briefing.select_current_morning_brew([old_brew], date(2026, 8, 26)))

    def test_current_morning_brew_is_selected_and_market_numbers_are_preserved(self):
        old = {"sender": "Morning Brew <crew@morningbrew.com>", "subject": "Morning Brew", "date": "Tue, 25 Aug 2026 08:00:00 +0000", "body": "S&P 500 up 9.9%"}
        current = {"sender": "Morning Brew <crew@morningbrew.com>", "subject": "Morning Brew", "date": "Wed, 26 Aug 2026 08:00:00 +0000", "body": "S&P 500 down 0.4%\nNasdaq Composite down 0.7%\nDow Jones down 0.1%\nMarkets fell because investors reacted to earnings."}
        self.assertIs(briefing.select_current_morning_brew([old, current], date(2026, 8, 26)), current)
        summary = self._brief(messages=[], brew=[old, current])
        self.assertIn("S&P 500 down 0.4%", summary)
        self.assertIn("Nasdaq Composite down 0.7%", summary)
        self.assertIn("Dow Jones down 0.1%", summary)
        self.assertIn("because investors reacted to earnings", summary)
        self.assertNotIn("9.9%", summary)

    def test_missing_weekend_and_market_holiday_brew_do_not_fabricate_market_data(self):
        self.assertIn("Today's edition was not found", self._brief())
        weekend_brew = {"sender": "Morning Brew <crew@morningbrew.com>", "subject": "Morning Brew", "date": "Sat, 29 Aug 2026 08:00:00 +0000", "body": "S&P 500 up 5%"}
        self.assertIn("Weekend:", self._brief(brew=[weekend_brew], now=datetime(2026, 8, 29, 9, tzinfo=timezone.utc)))
        holiday_brew = dict(weekend_brew, date="Fri, 03 Jul 2026 08:00:00 +0000")
        self.assertIn("markets are closed", self._brief(brew=[holiday_brew], now=datetime(2026, 7, 3, 9, tzinfo=timezone.utc)))

    def test_all_sections_order_weather_and_no_priority_recommendation(self):
        summary = self._brief()
        headings = ["Calendar today:", "Asana:", "Inbox:", "Morning Brew / Markets:", "Weather:"]
        self.assertEqual(sorted(summary.index(item) for item in headings), [summary.index(item) for item in headings])
        self.assertIn("high 80°F", summary)
        self.assertTrue(summary.endswith("That's the morning update. What would you like to work on?"))
        self.assertNotRegex(summary.lower(), r"prioriti[sz]e|you should do|focus on first")


if __name__ == "__main__":
    unittest.main()
