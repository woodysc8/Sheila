"""
Decides whether Iris is allowed to interrupt you right now:
  - Quiet hours (before 7:30am or after 6pm ET)
  - Weekends (fully silent)
  - Active calendar meetings (via your calendar's public ICS feed)
  - Manually-toggled "in a meeting" state (say "Iris, I'm in a meeting" /
    "Iris, meeting's over" to control this -- see main.py) -- catches ad
    hoc calls/meetings that were never on the calendar

Nothing here blocks Iris from responding when you talk to her -- it only
gates unprompted notifications (email alerts, etc).
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import requests
import icalendar
import recurring_ical_events
import config
import memory

ET = ZoneInfo("America/New_York")

_calendar_cache = {"data": None, "fetched_at": None}
_CACHE_TTL_SECONDS = 300  # re-fetch calendar at most every 5 minutes


def is_quiet_hours() -> bool:
    now = datetime.now(ET)
    if now.weekday() >= 5:  # Saturday=5, Sunday=6 -- fully quiet all weekend
        return True
    start = now.replace(hour=7, minute=30, second=0, microsecond=0)
    end = now.replace(hour=18, minute=0, second=0, microsecond=0)
    return not (start <= now <= end)


def _get_calendar():
    now = datetime.now()
    stale = (
        _calendar_cache["data"] is None
        or _calendar_cache["fetched_at"] is None
        or (now - _calendar_cache["fetched_at"]).total_seconds() > _CACHE_TTL_SECONDS
    )
    if stale:
        resp = requests.get(config.CALENDAR_ICS_URL, timeout=10)
        resp.raise_for_status()
        _calendar_cache["data"] = icalendar.Calendar.from_ical(resp.text)
        _calendar_cache["fetched_at"] = now
    return _calendar_cache["data"]


def _is_in_calendar_meeting() -> bool:
    if not config.CALENDAR_ICS_URL or "PUT_YOUR" in config.CALENDAR_ICS_URL:
        return False
    try:
        cal = _get_calendar()
        now = datetime.now(ET)
        window_start = now - timedelta(minutes=5)
        window_end = now + timedelta(minutes=5)
        events = recurring_ical_events.of(cal).between(window_start, window_end)
        for event in events:
            ev_start = event.get("DTSTART").dt
            ev_end = event.get("DTEND").dt
            if not hasattr(ev_start, "hour"):  # all-day event, skip
                continue
            if ev_start.tzinfo is None:
                ev_start = ev_start.replace(tzinfo=ET)
            if ev_end.tzinfo is None:
                ev_end = ev_end.replace(tzinfo=ET)
            if ev_start <= now <= ev_end:
                return True
        return False
    except Exception as e:
        print(f"[scheduler] Calendar check failed: {e}")
        return False  # fail open -- don't go silent just because the fetch broke


def is_in_meeting() -> bool:
    return memory.get_meeting_status() or _is_in_calendar_meeting()


def should_notify_now() -> bool:
    """The single check other modules should call before firing an
    unprompted notification."""
    if is_quiet_hours():
        return False
    if is_in_meeting():
        return False
    return True
