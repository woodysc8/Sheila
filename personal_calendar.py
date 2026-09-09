"""Sheila-facing personal-calendar functions and deterministic commands."""

from datetime import date, datetime, time, timedelta
import re
from zoneinfo import ZoneInfo

import calendar_store
import config


def get_personal_calendar_events(start: datetime, end: datetime) -> list[dict[str, object]]:
    return calendar_store.list_events(start, end)


def create_personal_calendar_event(title: str, start: datetime, end: datetime,
                                   description: str = "", location: str = "") -> dict[str, object]:
    return calendar_store.create_event(title, start, end, description=description, location=location)


def update_personal_calendar_event(event_id: int, **changes: object) -> dict[str, object] | None:
    return calendar_store.update_event(event_id, **changes)


def delete_personal_calendar_event(event_id: int) -> bool:
    return calendar_store.delete_event(event_id)


def _zone() -> ZoneInfo:
    return config.get_sheila_timezone()


def _date_from_text(text: str, now: datetime) -> date | None:
    lowered = text.lower()
    if "today" in lowered:
        return now.date()
    if "tomorrow" in lowered:
        return now.date() + timedelta(days=1)
    explicit = re.search(r"\b(?:on\s+)?([A-Z][a-z]+\s+\d{1,2}(?:,\s*\d{4})?)\b", text)
    if explicit:
        value = explicit.group(1)
        for pattern in ("%B %d, %Y", "%B %d", "%B %d %Y"):
            try:
                parsed = datetime.strptime(value, pattern).date()
                return parsed.replace(year=now.year) if pattern == "%B %d" else parsed
            except ValueError:
                continue
    weekdays = {name: index for index, name in enumerate(("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"))}
    for name, weekday in weekdays.items():
        if re.search(rf"\b(?:on\s+)?{name}\b", lowered):
            return now.date() + timedelta(days=(weekday - now.weekday()) % 7 or 7)
    return None


def _time_from_text(text: str, default_meridiem: str | None = None) -> time | None:
    match = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", text, re.IGNORECASE)
    if not match:
        return None
    hour, minute, meridiem = int(match.group(1)), int(match.group(2) or 0), (match.group(3) or default_meridiem or "").lower()
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def _find_events(title: str, now: datetime) -> list[dict[str, object]]:
    start = now - timedelta(days=366)
    end = now + timedelta(days=366)
    return [event for event in get_personal_calendar_events(start, end) if title.lower() in str(event["title"]).lower()]


def _event_line(event: dict[str, object]) -> str:
    start = str(event["start"]).replace("T", " ")
    end = str(event["end"]).replace("T", " ")
    return f"- {event['title']}: {start} to {end} ({event['timezone']})"


def _display_time(value: datetime) -> str:
    return value.strftime("%I:%M %p").lstrip("0")


def handle_personal_calendar_request(user_text: str, now: datetime | None = None) -> str:
    """Handle explicit personal-calendar commands without an LLM decision."""
    zone = _zone()
    current = (now or datetime.now(zone)).astimezone(zone)
    text = user_text.strip().rstrip("?.!")
    lowered = text.lower()
    if re.search(r"\b(?:cancel|delete|remove)\b", lowered):
        title = re.sub(r"^.*?\b(?:cancel|delete|remove)\s+(?:the\s+)?(?:event\s+)?", "", text, flags=re.IGNORECASE).strip()
        matches = _find_events(title, current)
        if len(matches) != 1:
            return "I need the exact personal-calendar event to delete." if not matches else "I found more than one matching event. Which one should I delete?"
        delete_personal_calendar_event(int(matches[0]["id"]))
        return f"Deleted personal-calendar event: {matches[0]['title']}."
    if re.search(r"\b(?:move|reschedule|change|update)\b", lowered):
        match = re.search(r"\b(?:move|reschedule|change|update)\s+(.+?)\s+to\s+(?:at\s+)?(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b", text, re.IGNORECASE)
        if not match:
            return "What event should I move, and what time should it have?"
        matches = _find_events(match.group(1).strip(), current)
        if len(matches) != 1:
            return "I need the exact personal-calendar event to move." if not matches else "I found more than one matching event. Which one should I move?"
        event = matches[0]
        start = datetime.fromisoformat(str(event["start"])).astimezone(zone)
        end = datetime.fromisoformat(str(event["end"])).astimezone(zone)
        default_meridiem = "pm" if start.hour >= 12 else "am"
        new_time = _time_from_text(f"at {match.group(2)}", default_meridiem=default_meridiem)
        moved_start = start.replace(hour=new_time.hour, minute=new_time.minute, second=0, microsecond=0)
        moved_end = moved_start + (end - start)
        update_personal_calendar_event(int(event["id"]), start=moved_start.isoformat(), end=moved_end.isoformat())
        return f"Moved {event['title']} to {_display_time(moved_start)} on {moved_start:%Y-%m-%d}."
    if re.search(r"\b(?:add|schedule|put|create|book)\b", lowered):
        command = re.sub(r"^.*?\b(?:add|schedule|put|create|book)\s+(?:an?\s+)?", "", text, flags=re.IGNORECASE)
        start_time = _time_from_text(command)
        event_date = _date_from_text(command, current)
        if not start_time or not event_date:
            return "Please specify the event date and time before I add it."
        title = re.split(r"\b(?:today|tomorrow|on\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|at\s+\d)", command, maxsplit=1, flags=re.IGNORECASE)[0].strip(" ,")
        if not title:
            return "Please give the event a title before I add it."
        start = datetime.combine(event_date, start_time, tzinfo=zone)
        end = start + timedelta(hours=1)
        event = create_personal_calendar_event(title, start, end)
        return f"Added {event['title']} to your personal calendar for {start:%Y-%m-%d} at {_display_time(start)}."
    if "tomorrow" in lowered:
        start = datetime.combine(current.date() + timedelta(days=1), time.min, tzinfo=zone)
        events = get_personal_calendar_events(start, start + timedelta(days=1))
    elif "today" in lowered:
        start = datetime.combine(current.date(), time.min, tzinfo=zone)
        events = get_personal_calendar_events(start, start + timedelta(days=1))
    else:
        start = datetime.combine(current.date(), time.min, tzinfo=zone)
        events = get_personal_calendar_events(start, start + timedelta(days=30))
    return "No personal-calendar events found." if not events else "Personal calendar:\n" + "\n".join(_event_line(event) for event in events)