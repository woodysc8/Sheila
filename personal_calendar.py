"""Sheila-facing personal-calendar functions and deterministic commands."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import json
import os
import re
from zoneinfo import ZoneInfo

import calendar_store
import config


def _log(operation: str, **details: object) -> None:
    payload = {"operation": operation, "pid": os.getpid(), "db_path": os.path.abspath(config.DB_PATH), **details}
    print(f"[personal_calendar] {json.dumps(payload, sort_keys=True, default=str)}", flush=True)


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


_WEEKDAYS = {name: index for index, name in enumerate(
    ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
)}
_MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"
_EXPLICIT_DATE = re.compile(
    rf"\b(?:on\s+)?(?P<month>{_MONTHS})\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s*(?P<year>\d{{4}}))?\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CalendarEventCandidate:
    """A deterministic, reviewable personal-calendar event before it is written."""

    title: str
    start: datetime
    end: datetime
    all_day: bool
    source: str


def resolve_calendar_date(text: str, now: datetime | None = None) -> date | None:
    """Resolve a calendar date in Sheila's timezone.

    Bare weekdays mean the next occurrence, including today. ``this Friday``
    means Friday in the current calendar week (or today on Friday); if that day
    has passed, it means the next occurrence so Sheila never creates a past
    event. ``next Friday`` always means the Friday in the following week.
    """
    zone = _zone()
    current = (now or datetime.now(zone)).astimezone(zone)
    lowered = text.lower()
    if "tonight" in lowered or "today" in lowered:
        return current.date()
    if "tomorrow" in lowered:
        return current.date() + timedelta(days=1)
    explicit = _EXPLICIT_DATE.search(text)
    if explicit:
        year = int(explicit.group("year") or current.year)
        try:
            parsed = date(year, datetime.strptime(explicit.group("month"), "%B").month, int(explicit.group("day")))
        except ValueError:
            return None
        # A month/day without a year normally refers to the next such date.
        if explicit.group("year") is None and parsed < current.date():
            parsed = parsed.replace(year=parsed.year + 1)
        return parsed
    for name, weekday in _WEEKDAYS.items():
        if re.search(rf"\b(?:this|next)\s+{name}\b", lowered):
            if re.search(rf"\bnext\s+{name}\b", lowered):
                days = (weekday - current.weekday()) % 7 + 7
            else:
                days = weekday - current.weekday()
                if days < 0:
                    days += 7
            return current.date() + timedelta(days=days)
        if re.search(rf"\b(?:on\s+)?{name}\b", lowered):
            return current.date() + timedelta(days=(weekday - current.weekday()) % 7)
    if "this weekend" in lowered or "next weekend" in lowered:
        days = (5 - current.weekday()) % 7
        if "next weekend" in lowered:
            days += 7
        return current.date() + timedelta(days=days)
    return None


def _date_from_text(text: str, now: datetime) -> date | None:
    return resolve_calendar_date(text, now)


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
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"[\s.?!]+$", "", cleaned)
    if cleaned.lower().startswith("dinner with ") or cleaned.lower().startswith("meeting "):
        return cleaned
    if re.search(r"\b(?:coming|dinner|appointment|game|trivia|plans?)\b", cleaned, re.IGNORECASE):
        return cleaned
    return f"{cleaned} plans" if cleaned else "Personal plan"


def _natural_event_subject(text: str) -> str:
    subject = re.sub(r"\b(?:actually|now|instead|is|are|coming|going|over|off|cancelled|canceled|anymore)\b", " ", text, flags=re.IGNORECASE)
    subject = re.sub(r"\b(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b", " ", subject, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", subject).strip(" .,!?\")")


_MATCH_STOPWORDS = {
    "a", "an", "and", "at", "for", "i", "is", "my", "on", "the", "to", "with",
}


def _event_match_tokens(value: object) -> set[str]:
    words = re.findall(r"[a-z0-9]+", str(value).lower())
    return {word for word in words if word not in _MATCH_STOPWORDS}


def _plan_time(text: str) -> time | None:
    return _time_from_text(text, default_meridiem="pm")


def _natural_lookup(text: str) -> bool:
    return bool(re.search(r"\b(?:what do I have|what am I doing|what's happening|what is happening|when is|show me my|coming up|what's on my)\b", text, re.IGNORECASE))


def _find_events(title: str, now: datetime, upcoming_only: bool = False, partial: bool = False, exact: bool = False, date_hint: date | None = None) -> list[dict[str, object]]:
    start = now - timedelta(days=366)
    end = now + timedelta(days=366)
    query_tokens = _event_match_tokens(title)
    events = get_personal_calendar_events(start, end)
    if upcoming_only:
        events = [event for event in events if datetime.fromisoformat(str(event["end"])).astimezone(_zone()) >= now]
    if date_hint is not None:
        events = [event for event in events if datetime.fromisoformat(str(event["start"])).astimezone(_zone()).date() == date_hint]
    matches = [event for event in events if query_tokens and (
        query_tokens & _event_match_tokens(event["title"])
        if partial else query_tokens == _event_match_tokens(event["title"])
        if exact else query_tokens <= _event_match_tokens(event["title"])
    )]
    _log("natural_event_match", event_ids=[event["id"] for event in matches])
    return matches


def _event_line(event: dict[str, object]) -> str:
    start = str(event["start"]).replace("T", " ")
    end = str(event["end"]).replace("T", " ")
    return f"- {event['title']}: {start} to {end} ({event['timezone']})"


def _log_lookup(events: list[dict[str, object]]) -> None:
    _log(
        "natural_lookup_result",
        event_ids=[event["id"] for event in events],
        starts=[event["start"] for event in events],
        ends=[event["end"] for event in events],
    )


def _display_time(value: datetime) -> str:
    return value.strftime("%I:%M %p").lstrip("0")


def _is_definite_commitment(text: str) -> bool:
    if "?" in text or _has_tentative_language(text) or _has_historical_language(text):
        return False
    return bool(re.search(
        r"\b(?:i(?:'m| am)\s+going|taking\s+pto|[a-z][a-z'-]*\s+is\s+coming|"
        r"i\s+have\s+(?:an?\s+)?(?:doctor'?s?\s+)?appointment|appointment|"
        r"company\s+retreat|alumni\s+weekend|\w+\s+game)\b",
        text,
        re.IGNORECASE,
    ))


def _event_title(statement: str) -> str:
    title = _EXPLICIT_DATE.sub("", statement)
    title = re.sub(r"\bto\s+(?:the\s+)?\d{1,2}(?:st|nd|rd|th)?\b", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\b(?:this|next)\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\b(?:on\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\bleaving\s+.*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\b(?:i'm|i am)\s+(?:taking\s+)?", "", title, flags=re.IGNORECASE)
    title = re.sub(r"^\s*going\s+to\s+", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+(?:in\s+the\s+)?morning\b", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+", " ", title).strip(" ,.-")
    coming = re.match(r"^(.+?)\s+is\s+coming(?:\s+up)?$", title, re.IGNORECASE)
    if coming:
        return f"{coming.group(1)} coming"
    return title or "Personal commitment"


def _date_range_from_statement(statement: str, now: datetime) -> tuple[date, date] | None:
    start_match = _EXPLICIT_DATE.search(statement)
    if not start_match:
        start = resolve_calendar_date(statement, now)
        if start is None:
            return None
        end_match = re.search(r"\bleaving\s+(?:on\s+)?(.+?)(?:\s+before\b|$)", statement, re.IGNORECASE)
        end = resolve_calendar_date(end_match.group(1), now) if end_match else start
        if end is not None and end < start:
            end += timedelta(days=7)
        return start, end or start
    start = resolve_calendar_date(start_match.group(0), now)
    range_match = re.search(
        r"\b(?:to|through|-)\s+(?:the\s+)?(?P<day>\d{1,2})(?:st|nd|rd|th)?\b",
        statement[start_match.end():], re.IGNORECASE,
    )
    if not start or not range_match:
        return (start, start) if start else None
    try:
        end = date(start.year, start.month, int(range_match.group("day")))
    except ValueError:
        return None
    if end < start:
        return None
    return start, end


def extract_definite_event_candidates(user_text: str, now: datetime | None = None) -> list[CalendarEventCandidate]:
    """Extract only definite, dated commitments; this function never writes."""
    zone = _zone()
    current = (now or datetime.now(zone)).astimezone(zone)
    candidates: list[CalendarEventCandidate] = []
    # Newlines are intentional event boundaries in the multi-event messages Sheila receives.
    statements = [part.strip(" \t.-") for part in re.split(r"[\r\n]+", user_text) if part.strip()]
    if len(statements) == 1:
        statements = [part.strip(" \t.-") for part in re.split(r"(?<=[.!])\s+", user_text) if part.strip()]
    for statement in statements:
        if not _is_definite_commitment(statement):
            continue
        dates = _date_range_from_statement(statement, current)
        if dates is None:
            continue
        start_date, last_date = dates
        start_time = _plan_time(statement)
        if start_time is None and re.search(r"\bmorning\b", statement, re.IGNORECASE):
            start_time = time(9, 0)
        all_day = start_time is None
        start = datetime.combine(start_date, start_time or time.min, tzinfo=zone)
        # Timed commitments default to one hour. All-day end is exclusive.
        end = (start + timedelta(hours=1)) if not all_day else datetime.combine(last_date + timedelta(days=1), time.min, tzinfo=zone)
        candidates.append(CalendarEventCandidate(_event_title(statement), start, end, all_day, statement))
    return candidates


def _create_definite_event_candidates(user_text: str, now: datetime) -> str | None:
    candidates = extract_definite_event_candidates(user_text, now)
    if not candidates:
        return None
    created: list[str] = []
    failed: list[str] = []
    for candidate in candidates:
        try:
            event = create_personal_calendar_event(candidate.title, candidate.start, candidate.end)
        except (calendar_store.CalendarError, OSError, ValueError):
            failed.append(candidate.title)
            continue
        created.append(str(event["title"]))
    messages: list[str] = []
    if created:
        # Keep the established acknowledgement for the original one-line
        # relative-plan form, while the new batch path reports confirmed writes.
        if len(created) == 1 and not _EXPLICIT_DATE.search(user_text) and "\n" not in user_text and "leaving" not in user_text.lower() and not failed:
            return "Got it."
        noun = "event" if len(created) == 1 else "events"
        messages.append(f"Added {len(created)} personal-calendar {noun}: " + "; ".join(created) + ".")
    if failed:
        messages.append("I couldn't add: " + "; ".join(failed) + ".")
    return " ".join(messages)


def handle_personal_calendar_request(user_text: str, now: datetime | None = None) -> str:
    """Handle explicit personal-calendar commands without an LLM decision."""
    zone = _zone()
    current = (now or datetime.now(zone)).astimezone(zone)
    text = user_text.strip().rstrip("?.!")
    lowered = text.lower()
    if (_has_tentative_language(text) or _has_historical_language(text)) and re.search(r"\b(?:actually|instead|now|move|reschedule|change|update)\b", lowered):
        return "I didn't change the calendar because that sounded tentative or historical."
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
        matches = _find_events(subject, current, upcoming_only=True, partial=True)
        if len(matches) == 1:
            event = matches[0]
            start = datetime.fromisoformat(str(event["start"])).astimezone(zone)
            end = datetime.fromisoformat(str(event["end"])).astimezone(zone)
            new_time = _time_from_text(text, default_meridiem="pm" if start.hour >= 12 else "am")
            moved_start = start.replace(hour=new_time.hour, minute=new_time.minute, second=0, microsecond=0)
            updated = update_personal_calendar_event(
                int(event["id"]),
                start=moved_start.isoformat(),
                end=(moved_start + (end - start)).isoformat(),
            )
            if updated is None:
                return "I couldn't update that personal-calendar event."
            persisted_start = datetime.fromisoformat(str(updated["start"])).astimezone(zone)
            _log(
                "natural_update_readback",
                event_id=updated["id"],
                persisted_start=updated["start"],
                persisted_end=updated["end"],
            )
            return f"Updated it to {_display_time(persisted_start)}."
        if len(matches) > 1:
            return "Which matching personal-calendar event do you mean?"
    if _natural_lookup(text):
        named = re.search(r"\bwhen is\s+(.+?)(?:\?|$)", text, re.IGNORECASE)
        if named:
            matches = _find_events(named.group(1).strip(), current)
            _log_lookup(matches)
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
        _log_lookup(events)
        return "No personal-calendar events found." if not events else "Personal calendar:\n" + "\n".join(_event_line(event) for event in events)
    committed = _create_definite_event_candidates(text, current)
    if committed is not None:
        return committed
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
            matches = _find_events(title, current, exact=True, date_hint=event_date)
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
