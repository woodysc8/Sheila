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
    if "tonight" in lowered or "today" in lowered:
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
    if "this weekend" in lowered or "next weekend" in lowered:
        days_until_saturday = (5 - now.weekday()) % 7 or 7
        return now.date() + timedelta(days=days_until_saturday + (7 if "next weekend" in lowered else 0))
    return None


def _time_from_text(text: str, default_meridiem: str | None = None) -> time | None:
    match = re.search(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", text, re.IGNORECASE)
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


def _has_tentative_language(text: str) -> bool:
    return bool(re.search(r"\b(?:might|may|maybe|thinking about|wish|should I|if)\b", text, re.IGNORECASE))


def _has_historical_language(text: str) -> bool:
    return bool(re.search(r"\b(?:went|came|was|were|did|last|yesterday|already)\b", text, re.IGNORECASE))


def _has_date(text: str) -> bool:
    return bool(re.search(r"\b(?:today|tonight|tomorrow|this weekend|next weekend|next|monday|tuesday|wednesday|thursday|friday|saturday|sunday|january|february|march|april|may|june|july|august|september|october|november|december)\b", text, re.IGNORECASE))


def _has_time(text: str) -> bool:
    return bool(re.search(r"\b(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm)\b|\bat\s+\d{1,2}(?::\d{2})?\b", text, re.IGNORECASE))


def _is_definite_plan(text: str) -> bool:
    lowered = text.lower()
    if _has_tentative_language(text) or _has_historical_language(text) or "?" in text:
        return False
    definite = re.search(r"\b(?:is coming|are coming|has|have|am going|is going|are going|meeting|dinner|appointment|game|plans?)\b", lowered)
    return bool(definite and _has_date(text))


def _plan_title(text: str) -> str:
    cleaned = re.sub(r"\b(?:at)\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:today|tonight|tomorrow|this weekend|next weekend|on\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:next|this)\s+weekend\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^(?:actually\s+)?(?:i\s+have|i'm\s+going\s+to|i am\s+going\s+to|my|meeting)\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+(?:is|are)\s+(?:actually\s+)?(?:coming|going)(?:\s+over)?\b", " coming", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,!?\")")
    if cleaned.lower().startswith("dinner with ") or cleaned.lower().startswith("meeting "):
        return cleaned
    if re.search(r"\b(?:coming|dinner|appointment|game|trivia|plans?)\b", cleaned, re.IGNORECASE):
        return cleaned
    return f"{cleaned} plans" if cleaned else "Personal plan"


def _natural_event_subject(text: str) -> str:
    subject = re.sub(r"\b(?:actually|now|instead|is|are|coming|going|over|off|cancelled|canceled|anymore)\b", " ", text, flags=re.IGNORECASE)
    subject = re.sub(r"\b(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b", " ", subject, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", subject).strip(" .,!?\")")


def _plan_time(text: str) -> time | None:
    return _time_from_text(text, default_meridiem="pm")


def _natural_lookup(text: str) -> bool:
    return bool(re.search(r"\b(?:what do I have|what am I doing|what's happening|what is happening|when is|show me my|coming up|what's on my)\b", text, re.IGNORECASE))


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
    if re.search(r"\b(?:cancel|delete|remove)\b|\b(?:isn't|is not|aren't|are not)\b.*\b(?:anymore|off|cancel)|\b(?:is|are)\b.*\b(?:off|cancelled|canceled|anymore)", lowered):
        title = re.sub(r"^.*?\b(?:cancel|delete|remove)\s+(?:the\s+)?(?:event\s+)?", "", text, flags=re.IGNORECASE).strip()
        if title == text:
            title = _natural_event_subject(text)
        matches = _find_events(title, current)
        if len(matches) != 1:
            return "I need the exact personal-calendar event to delete." if not matches else "I found more than one matching event. Which one should I delete?"
        delete_personal_calendar_event(int(matches[0]["id"]))
        return f"Deleted personal-calendar event: {matches[0]['title']}."
    if re.search(r"\b(?:move|reschedule|change|update)\b", lowered):
        match = re.search(r"\b(?:move|reschedule|change|update)\s+(.+?)\s+to\s+(?:at\s+)?(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b", text, re.IGNORECASE)
        date_match = re.search(r"\b(?:move|reschedule|change|update)\s+(.+?)\s+to\s+(?:this\s+|next\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow)\b", text, re.IGNORECASE)
        if not match and date_match:
            matches = _find_events(date_match.group(1).strip(), current)
            if len(matches) != 1:
                return "I need the exact personal-calendar event to move." if not matches else "I found more than one matching event. Which one should I move?"
            event = matches[0]
            start = datetime.fromisoformat(str(event["start"])).astimezone(zone)
            end = datetime.fromisoformat(str(event["end"])).astimezone(zone)
            new_date = _date_from_text(date_match.group(2), current)
            moved_start = start.replace(year=new_date.year, month=new_date.month, day=new_date.day)
            update_personal_calendar_event(int(event["id"]), start=moved_start.isoformat(), end=(moved_start + (end - start)).isoformat())
            return f"Moved {event['title']} to {moved_start:%Y-%m-%d}."
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
    if re.search(r"\b(?:actually|instead|now)\b", lowered) and _has_time(text):
        subject = _natural_event_subject(text)
        matches = _find_events(subject, current)
        if len(matches) == 1:
            event = matches[0]
            start = datetime.fromisoformat(str(event["start"])).astimezone(zone)
            end = datetime.fromisoformat(str(event["end"])).astimezone(zone)
            new_time = _time_from_text(text, default_meridiem="pm" if start.hour >= 12 else "am")
            moved_start = start.replace(hour=new_time.hour, minute=new_time.minute, second=0, microsecond=0)
            update_personal_calendar_event(int(event["id"]), start=moved_start.isoformat(), end=(moved_start + (end - start)).isoformat())
            return f"Updated it to {_display_time(moved_start)}."
        if len(matches) > 1:
            return "Which matching personal-calendar event do you mean?"
    if _natural_lookup(text):
        named = re.search(r"\bwhen is\s+(.+?)(?:\?|$)", text, re.IGNORECASE)
        if named:
            matches = _find_events(named.group(1).strip(), current)
            return "No personal-calendar events found." if not matches else "Personal calendar:\n" + "\n".join(_event_line(event) for event in matches)
        if "tomorrow" in lowered:
            start = datetime.combine(current.date() + timedelta(days=1), time.min, tzinfo=zone)
            events = get_personal_calendar_events(start, start + timedelta(days=1))
        elif "today" in lowered:
            start = datetime.combine(current.date(), time.min, tzinfo=zone)
            events = get_personal_calendar_events(start, start + timedelta(days=1))
        elif "weekend" in lowered:
            days_until_saturday = (5 - current.weekday()) % 7
            start = datetime.combine(current.date() + timedelta(days=days_until_saturday), time.min, tzinfo=zone)
            events = get_personal_calendar_events(start, start + timedelta(days=2))
        elif any(day in lowered for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")):
            event_date = _date_from_text(text, current)
            start = datetime.combine(event_date, time.min, tzinfo=zone)
            events = get_personal_calendar_events(start, start + timedelta(days=1))
        else:
            start = datetime.combine(current.date(), time.min, tzinfo=zone)
            events = get_personal_calendar_events(start, start + timedelta(days=30))
        return "No personal-calendar events found." if not events else "Personal calendar:\n" + "\n".join(_event_line(event) for event in events)
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
    if _is_definite_plan(text):
        event_date = _date_from_text(text, current)
        start_time = _plan_time(text) or time(9, 0)
        if event_date:
            title = _plan_title(text)
            start = datetime.combine(event_date, start_time, tzinfo=zone)
            matches = _find_events(title, current)
            if len(matches) == 1:
                event = matches[0]
                old_start = datetime.fromisoformat(str(event["start"])).astimezone(zone)
                old_end = datetime.fromisoformat(str(event["end"])).astimezone(zone)
                update_personal_calendar_event(int(event["id"]), start=start.isoformat(), end=(start + (old_end - old_start)).isoformat())
                return "Updated it."
            if len(matches) > 1:
                return "Which matching personal-calendar event do you mean?"
            event = create_personal_calendar_event(title, start, start + timedelta(hours=1))
            return "Got it."
    if "tomorrow" in lowered:
        start = datetime.combine(current.date() + timedelta(days=1), time.min, tzinfo=zone)
        events = get_personal_calendar_events(start, start + timedelta(days=1))
    elif "today" in lowered:
        start = datetime.combine(current.date(), time.min, tzinfo=zone)
        events = get_personal_calendar_events(start, start + timedelta(days=1))
    elif "weekend" in lowered:
        days_until_saturday = (5 - current.weekday()) % 7
        start = datetime.combine(current.date() + timedelta(days=days_until_saturday), time.min, tzinfo=zone)
        events = get_personal_calendar_events(start, start + timedelta(days=2))
    elif any(day in lowered for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")):
        event_date = _date_from_text(text, current)
        start = datetime.combine(event_date, time.min, tzinfo=zone)
        events = get_personal_calendar_events(start, start + timedelta(days=1))
    else:
        start = datetime.combine(current.date(), time.min, tzinfo=zone)
        events = get_personal_calendar_events(start, start + timedelta(days=30))
    return "No personal-calendar events found." if not events else "Personal calendar:\n" + "\n".join(_event_line(event) for event in events)