import unittest
from unittest.mock import patch
import briefing


class MorningBriefingTests(unittest.TestCase):
    def test_build_morning_briefing_returns_structured_summary(self):
        with patch.object(briefing.memory, "get_pending_notifications", return_value=[("2026-01-01T00:00:00", "email", "Alice emailed you")]), \
             patch.object(briefing, "get_asana_overdue_tasks", return_value=[]), \
             patch.object(briefing, "get_todays_calendar_events", return_value=[]), \
             patch.object(briefing, "get_weather_summary", return_value="Weather in Providence is 72F and clear."), \
             patch.object(briefing.memory, "mark_notifications_delivered"), \
             patch.object(briefing, "_summarize_with_ai", return_value=None):
            summary = briefing.build_morning_briefing()
            self.assertIn("Recent email activity", summary)
            self.assertIn("No overdue Asana tasks", summary)
            self.assertIn("Weather", summary)

    def test_extract_market_threads_keeps_morning_brew_lines(self):
        lines = briefing._extract_market_threads("Morning Brew Daily\nMarkets are mixed")
        self.assertEqual(lines, ["Morning Brew Daily", "Markets are mixed"])


if __name__ == "__main__":
    unittest.main()
