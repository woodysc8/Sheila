"""Fact-grounded Morning Protocol v2 over Sheila's canonical integrations."""

from datetime import date, datetime, time, timedelta
from email.utils import parsedate_to_datetime
import re

import requests

from integrations import asana, calendar, gmail
from integrations.google_auth import GoogleAuthError
import memory


def _today_range(now: datetime | None = None) -> tuple[datetime, datetime]:
    local_now = now or datetime.now().astimezone()
    start = datetime.combine(local_now.date(), time.min, tzinfo=local_now.tzinfo)
    return start, start + timedelta(days=1)


def _gmail_date_query(start: datetime, end: datetime, extra: str = "") -> str:
    return f"{extra} after:{start:%Y/%m/%d} before:{end:%Y/%m/%d}".strip()


def get_todays_gmail_messages(now: datetime | None = None) -> list[dict[str, str]]:
    start, end = _today_range(now)
    return gmail.search_messages(_gmail_date_query(start, end, "in:inbox"), limit=10)


def get_todays_calendar_events(now: datetime | None = None) -> list[dict[str, str]]:
    start, end = _today_range(now)
    return calendar.get_events(start, end, limit=20)


def _event_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def _format_event(event: dict[str, str]) -> tuple[str, datetime | None]:
    title = event.get("title", "(untitled event)")
    location = f" ({event['location']})" if event.get("location") else ""
    start_value = event.get("start", "")
    start_at, end_at = _event_datetime(start_value), _event_datetime(event.get("end", ""))
    if len(start_value) == 10 or not start_at:
        return f"- All day: {title}{location}", None
    local_start = start_at.astimezone()
    local_end = end_at.astimezone() if end_at else None
    end_text = local_end.strftime("%I:%M %p").lstrip("0") if local_end else ""
    return f"- {local_start.strftime('%I:%M %p').lstrip('0')}–{end_text}: {title}{location}", local_start


def _task_due_date(task: dict[str, object]) -> date | None:
    due_on = task.get("due_on")
    if isinstance(due_on, str) and due_on:
        try:
            return date.fromisoformat(due_on)
        except ValueError:
            return None
    due_at = task.get("due_at")
    if isinstance(due_at, str) and due_at:
        try:
            return datetime.fromisoformat(due_at.replace("Z", "+00:00")).astimezone().date()
        except ValueError:
            return None
    return None


def _tasks_due_on(tasks: list[dict[str, object]], target_date: date) -> list[dict[str, object]]:
    return [task for task in tasks if not task.get("completed") and _task_due_date(task) == target_date]


def _format_task(task: dict[str, object]) -> str:
    due = task.get("due_on") or task.get("due_at")
    project = f" — {task['project']}" if task.get("project") else ""
    return f"- {task.get('name', '(unnamed task)')} (due {due}){project}"


def _format_email(message: dict[str, str]) -> str:
    return f"- From: {message.get('sender', '')} | Subject: {message.get('subject', '(no subject)')} | Date: {message.get('date', '')}"


def _message_local_date(message: dict[str, str]) -> date | None:
    try:
        return parsedate_to_datetime(message.get("date", "")).astimezone().date()
    except (TypeError, ValueError, IndexError):
        return None


def _is_morning_brew(message: dict[str, str]) -> bool:
    haystack = " ".join((message.get("sender", ""), message.get("subject", ""))).lower()
    return "morning brew" in haystack or "morningbrew.com" in haystack


def select_current_morning_brew(messages: list[dict[str, str]], current_date: date) -> dict[str, str] | None:
    """Select only an edition whose message date is today; never reuse old mail."""
    return next((message for message in messages if _is_morning_brew(message) and _message_local_date(message) == current_date), None)


def get_current_morning_brew(now: datetime | None = None) -> dict[str, str] | None:
    start, end = _today_range(now)
    query = _gmail_date_query(start, end, "(from:morningbrew.com OR subject:\"Morning Brew\")")
    return select_current_morning_brew(gmail.search_messages(query, limit=5), start.date())


def _market_holidays(year: int) -> set[date]:
    """NYSE full-day closure dates using US market holiday rules for this year."""
    def observed(day: date) -> date:
        return day - timedelta(days=1) if day.weekday() == 5 else day + timedelta(days=1) if day.weekday() == 6 else day

    def nth_weekday(month: int, weekday: int, nth: int) -> date:
        day = date(year, month, 1)
        return day + timedelta(days=(weekday - day.weekday()) % 7 + 7 * (nth - 1))

    def last_weekday(month: int, weekday: int) -> date:
        day = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
        return day - timedelta(days=(day.weekday() - weekday) % 7)

    a, b = year % 19, year // 100
    c, d, e = year % 100, b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    easter = date(year, (h + l - 7 * m + 114) // 31, (h + l - 7 * m + 114) % 31 + 1)
    return {
        observed(date(year, 1, 1)), nth_weekday(1, 0, 3), nth_weekday(2, 0, 3), easter - timedelta(days=2),
        last_weekday(5, 0), observed(date(year, 6, 19)), observed(date(year, 7, 4)), nth_weekday(9, 0, 1),
        nth_weekday(11, 3, 4), observed(date(year, 12, 25)),
    }


def is_us_market_holiday(day: date) -> bool:
    return day in _market_holidays(day.year)


def _market_value(text: str, labels: tuple[str, ...]) -> str | None:
    label = "(?:" + "|".join(re.escape(item) for item in labels) + ")"
    match = re.search(rf"\b{label}\b[^\n.]*?(?:[+-]?\d+(?:\.\d+)?%)", text, re.IGNORECASE)
    return match.group(0).strip() if match else None


def _market_lines(message: dict[str, str], current_date: date) -> list[str]:
    if current_date.weekday() >= 5:
        return ["- Weekend: no market briefing is included without a current relevant edition."]
    if is_us_market_holiday(current_date):
        return ["- U.S. markets are closed today; no normal previous-session market move is presented."]
    text = "\n".join((message.get("subject", ""), message.get("snippet", ""), message.get("body", "")))
    lines: list[str] = []
    for title, labels in (("S&P 500", ("S&P 500", "S&P")), ("Nasdaq Composite", ("Nasdaq Composite", "Nasdaq")), ("Dow Jones Industrial Average", ("Dow Jones", "Dow"))):
        value = _market_value(text, labels)
        lines.append(f"- {title}: {value}" if value else f"- {title}: not found in today's edition.")
    why = next((line.strip() for line in text.splitlines() if re.search(r"\b(?:because|as .*?(?:investors|markets)|after .*?(?:investors|markets))\b", line, re.I)), None)
    lines.append(f"- Why: {why}" if why else "- Why: not found in today's edition.")
    return lines


def get_weather_summary() -> str:
    try:
        response = requests.get("https://wttr.in/Providence,RI?format=j1", timeout=20)
        response.raise_for_status()
        payload = response.json()
        current = payload.get("current_condition", [{}])[0]
        forecast = payload.get("weather", [{}])[0]
        description = current.get("weatherDesc", [{}])[0].get("value", "unclear").lower()
        current_f, high_f = current.get("temp_F", "?"), forecast.get("maxtempF", "?")
        rain = forecast.get("hourly", [{}])[0].get("chanceofrain", "")
        precipitation = f", {rain}% chance of rain" if rain and rain != "0" else ""
        return f"Providence: {description}, {current_f}°F now; high {high_f}°F{precipitation}."
    except requests.RequestException as exc:
        print(f"[briefing] Weather lookup failed: {exc}")
        return "Weather unavailable."
    except (TypeError, ValueError, KeyError, IndexError) as exc:
        print(f"[briefing] Weather response invalid: {exc}")
        return "Weather unavailable."


def build_morning_briefing(now: datetime | None = None) -> str:
    """Build a deterministic information brief; integrations determine all facts."""
    start, _end = _today_range(now)
    today = start.date()
    parts = [f"Morning briefing for {today.isoformat()}."]

    parts.append("\nCalendar today:")
    try:
        events = get_todays_calendar_events(start)
        parts.append(f"- {len(events)} event(s)")
        timed: list[tuple[datetime, str]] = []
        for event in events:
            line, event_start = _format_event(event)
            parts.append(line)
            if event_start:
                timed.append((event_start, event.get("title", "(untitled event)")))
        if timed:
            first_start, first_title = min(timed, key=lambda item: item[0])
            parts.append(f"First meeting: {first_start.strftime('%I:%M %p').lstrip('0')} — {first_title}.")
    except GoogleAuthError:
        parts.append("- Google Calendar isn't available right now.")

    parts.append("\nAsana:")
    try:
        overdue = asana.get_overdue_tasks(limit=20, current_date=today)
        tasks = asana.get_tasks(limit=20)
        task_groups = (
            ("Overdue tasks", overdue, "No overdue tasks found."),
            ("Tasks due today", _tasks_due_on(tasks, today), "No tasks due today."),
            ("Tasks due tomorrow", _tasks_due_on(tasks, today + timedelta(days=1)), "No tasks due tomorrow."),
        )
        for label, matching, empty in task_groups:
            parts.append(f"- {label}: {len(matching)}")
            parts.extend(_format_task(task) for task in matching) if matching else parts.append(f"  {empty}")
    except asana.AsanaError:
        parts.append("- Unable to retrieve Asana right now.")

    parts.append("\nInbox:")
    try:
        messages = get_todays_gmail_messages(start)
        inbox_messages = [message for message in messages if not _is_morning_brew(message)]
        parts.extend(_format_email(message) for message in inbox_messages) if inbox_messages else parts.append("- No non-Morning Brew messages found for today.")
    except GoogleAuthError:
        parts.append("- Google email isn't available right now.")

    parts.append("\nMorning Brew / Markets:")
    try:
        edition = get_current_morning_brew(start)
        parts.extend(_market_lines(edition, today) if edition else ["- Today's edition was not found in the inbox, so I don't have verified market figures from it."])
    except GoogleAuthError:
        parts.append("- Gmail isn't available, so Morning Brew market figures could not be verified.")

    parts.append(f"\nWeather:\n- {get_weather_summary()}")
    parts.append("\nThat's the morning update. What would you like to work on?")
    memory.mark_notifications_delivered()
    return "\n".join(parts)


if __name__ == "__main__":
    print(build_morning_briefing())
