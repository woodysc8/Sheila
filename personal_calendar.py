"""Sheila-facing personal-calendar functions and deterministic commands."""

from dataclasses import dataclass
from calendar import monthrange
from datetime import date, datetime, time, timedelta
import json
import os
import re
from zoneinfo import ZoneInfo

import calendar_store
import config
from integrations.calendar import GoogleCalendarAdapter


# Ephemeral conversational reference only.  It stores a Google event ID after
# a confirmed mutation; Google is still consulted before any follow-up action.
_last_confirmed_event_id: str | None = None
# Candidate IDs are retained only while Sheila is clarifying an ambiguous
# lookup. Event bodies are never retained; every answer re-reads Google.
_ambiguous_event_ids: tuple[str, ...] = ()

# These are deliberately narrow, context-only turns. They never contain an
# event title, so they are safe to resolve only through the ephemeral Google
# event ID established by the immediately preceding calendar interaction.
_EVENT_DETAIL_FOLLOWUP_PATTERN = re.compile(
    r"^\s*(?:when|what\s+time|what\s+day|where|what(?:'s|\s+is)\s+the\s+location|who\s+is\s+(?:it|this)\s+with)\s*[?.!]*\s*$",
    re.IGNORECASE,
)
_AMBIGUOUS_EVENT_LIST_PATTERN = re.compile(
    r"^\s*(?:what\s+events|which\s+ones|show\s+me\s+(?:them|the\s+events)|what\s+are\s+they)\s*[?.!]*\s*$",
    re.IGNORECASE,
)
_AMBIGUOUS_EVENT_ORDINAL_PATTERN = re.compile(
    r"^\s*(?:the\s+)?(?P<ordinal>first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)(?:\s+(?:one|event))?\s*[?.!]*\s*$",
    re.IGNORECASE,
)

# These are destination entities, not loose keyword expansion.  In
# particular, ``DR`` is recognized only as a standalone user-query alias and
# never used as a broad event-text token.
_TRIP_DESTINATION_ALIASES: tuple[tuple[str, ...], ...] = (
    ("dominican republic", "santo domingo", "punta cana", "sdq"),
)


def _log(operation: str, **details: object) -> None:
    payload = {"operation": operation, "pid": os.getpid(), "db_path": os.path.abspath(config.DB_PATH), **details}
    print(f"[personal_calendar] {json.dumps(payload, sort_keys=True, default=str)}", flush=True)


def _google_calendar() -> GoogleCalendarAdapter | None:
    """Use the configured Google Calendar ID; retain legacy compatibility for an ICS URL."""
    calendar_id = config.SHEILA_PERSONAL_GOOGLE_CALENDAR_ID.strip()
    if not calendar_id or "://" in calendar_id:
        return None
    return GoogleCalendarAdapter(calendar_id)


def get_personal_calendar_events(start: datetime, end: datetime) -> list[dict[str, object]]:
    adapter = _google_calendar()
    if adapter:
        result = adapter.list_events(start, end, limit=10000)
        _log("google_list", success=result.success, count=len(result.value) if result.success else 0)
        if not result.success:
            raise calendar_store.CalendarError(result.error or "Personal Google Calendar is unavailable.")
        return result.value
    return calendar_store.list_events(start, end)


def _trip_destination_aliases(user_text: str) -> tuple[str, ...] | None:
    normalized = user_text.lower()
    for aliases in _TRIP_DESTINATION_ALIASES:
        if any(re.search(rf"\b{re.escape(alias)}\b", normalized) for alias in aliases):
            return aliases
    if re.search(r"\bdr\b", normalized):
        return _TRIP_DESTINATION_ALIASES[0]
    return None


def find_existing_trip_events(user_text: str, now: datetime | None = None) -> list[dict[str, object]] | None:
    """Find upcoming personal-calendar events for a recognized destination.

    ``None`` means the request did not name a supported destination entity;
    an empty list means it did but Google Calendar has no matching event.
    """
    aliases = _trip_destination_aliases(user_text)
    if aliases is None:
        return None
    zone = _zone()
    current = (now or datetime.now(zone)).astimezone(zone)
    events = get_personal_calendar_events(
        datetime.combine(current.date(), time.min, tzinfo=zone),
        datetime.combine(current.date() + timedelta(days=730), time.min, tzinfo=zone),
    )
    matches: list[dict[str, object]] = []
    for event in events:
        searchable = " ".join(str(event.get(field) or "") for field in ("title", "location", "description")).lower()
        if any(re.search(rf"\b{re.escape(alias)}\b", searchable) for alias in aliases):
            matches.append(event)
    return sorted(matches, key=lambda event: str(event.get("start", "")))


def format_existing_trip_events(events: list[dict[str, object]]) -> str:
    """Present destination matches from Google Calendar without inventing data."""
    if not events:
        return "No matching personal Google Calendar trip events found."
    lines: list[str] = []
    for event in events:
        title = str(event.get("title") or "(untitled event)")
        location = str(event.get("location") or "").strip()
        try:
            start = datetime.fromisoformat(str(event["start"])).astimezone(_zone())
            timing = (f"{start.strftime('%A, %B %d, %Y').replace(' 0', ' ')} (all-day)"
                      if event.get("all_day") else
                      f"{start.strftime('%A, %B %d, %Y').replace(' 0', ' ')} at {_display_time(start)}")
        except (KeyError, ValueError):
            timing = str(event.get("start") or "unknown time")
        lines.append(f"- {title} — {timing}" + (f" — {location}" if location else ""))
    return "Personal Google Calendar:\n" + "\n".join(lines)


def create_personal_calendar_event(title: str, start: datetime, end: datetime,
                                   description: str = "", location: str = "", all_day: bool = False) -> dict[str, object]:
    adapter = _google_calendar()
    if adapter:
        result = adapter.create_event(title, start.date() if all_day else start, end.date() if all_day else end,
                                      description, location)
        _log("google_create", success=result.success, all_day=all_day)
        if not result.success:
            raise calendar_store.CalendarError(result.error or "Personal Google Calendar write failed.")
        _remember_confirmed_event(result.value)
        return result.value
    return calendar_store.create_event(title, start, end, description=description, location=location)


def update_personal_calendar_event(event_id: int | str, **changes: object) -> dict[str, object] | None:
    adapter = _google_calendar()
    if adapter:
        existing = adapter.find_event(str(event_id))
        if not existing.success:
            _log("google_update", success=False)
            return None
        event = existing.value
        all_day = bool(event.get("all_day"))
        start, end = changes.get("start", event["start"]), changes.get("end", event["end"])
        if all_day:
            start, end = date.fromisoformat(str(start)[:10]), date.fromisoformat(str(end)[:10])
        result = adapter.update_event(str(event_id), str(changes.get("title", event["title"])), start, end,
                                      str(changes.get("description", event.get("description", ""))),
                                      str(changes.get("location", event.get("location", ""))))
        _log("google_update", success=result.success)
        if result.success:
            _remember_confirmed_event(result.value)
        return result.value if result.success else None
    return calendar_store.update_event(event_id, **changes)


def delete_personal_calendar_event(event_id: int | str) -> bool:
    adapter = _google_calendar()
    if adapter:
        result = adapter.delete_event(str(event_id))
        _log("google_delete", success=result.success)
        if result.success:
            _clear_confirmed_event(str(event_id))
        return result.success
    deleted = calendar_store.delete_event(event_id)
    if deleted:
        _clear_confirmed_event(str(event_id))
    return deleted


def _zone() -> ZoneInfo:
    return config.get_sheila_timezone()


def _remember_confirmed_event(event: dict[str, object]) -> None:
    global _last_confirmed_event_id, _ambiguous_event_ids
    event_id = str(event.get("id", ""))
    if event_id:
        _last_confirmed_event_id = event_id
        _ambiguous_event_ids = ()


def _clear_confirmed_event(event_id: str) -> None:
    global _last_confirmed_event_id
    if _last_confirmed_event_id == event_id:
        _last_confirmed_event_id = None


def _clear_active_event_reference() -> None:
    """Discard an ambiguous or no-longer-valid conversational event reference."""
    global _last_confirmed_event_id
    _last_confirmed_event_id = None


def _remember_ambiguous_events(events: list[dict[str, object]]) -> None:
    """Keep only candidate Google IDs while the user chooses one."""
    global _last_confirmed_event_id, _ambiguous_event_ids
    _last_confirmed_event_id = None
    _ambiguous_event_ids = tuple(str(event["id"]) for event in events if event.get("id"))


def _clear_ambiguous_event_reference() -> None:
    global _ambiguous_event_ids
    _ambiguous_event_ids = ()


def has_ambiguous_event_reference() -> bool:
    return bool(_ambiguous_event_ids)


def clear_ambiguous_event_reference() -> None:
    """Clear process-local ambiguity context when the topic changes."""
    _clear_ambiguous_event_reference()


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


def _time_range_from_text(text: str) -> tuple[time, time] | None:
    """Parse an explicit range such as ``3-4pm`` without guessing its duration."""
    match = re.search(
        r"\b(\d{1,2})(?::(\d{2}))?\s*-\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    first_hour, first_minute = int(match.group(1)), int(match.group(2) or 0)
    last_hour, last_minute = int(match.group(3)), int(match.group(4) or 0)
    if not all((1 <= first_hour <= 12, 1 <= last_hour <= 12, first_minute < 60, last_minute < 60)):
        return None
    if match.group(5).lower() == "pm":
        first_hour = first_hour + 12 if first_hour < 12 else first_hour
        last_hour = last_hour + 12 if last_hour < 12 else last_hour
    else:
        first_hour = 0 if first_hour == 12 else first_hour
        last_hour = 0 if last_hour == 12 else last_hour
    return time(first_hour, first_minute), time(last_hour, last_minute)


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
    # A date range such as "October 21-23" is not an evening appointment.
    # Keep such travel/PTO commitments all-day unless a real clock time is
    # supplied elsewhere in the statement.
    if _EXPLICIT_DATE.search(text) and re.search(r"\b(?:to|through|-)\s*\d{1,2}(?:st|nd|rd|th)?\b", text, re.IGNORECASE):
        if not re.search(r"\b(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm)\b", text, re.IGNORECASE):
            return None
    return _time_from_text(text, default_meridiem="pm")


def _natural_lookup(text: str) -> bool:
    return bool(re.search(
        r"\b(?:what do I have|what am I doing|what's happening|what is happening|when is|when am i going to|do i have|show me my|coming up|what's on my)\b",
        text,
        re.IGNORECASE,
    ))


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
    _log("natural_event_match", count=len(matches))
    return matches


def _event_line(event: dict[str, object]) -> str:
    start = str(event["start"]).replace("T", " ")
    end = str(event["end"]).replace("T", " ")
    return f"- {event['title']}: {start} to {end} ({event.get('timezone') or 'America/New_York'})"


def _log_lookup(events: list[dict[str, object]]) -> None:
    _log("natural_lookup_result", count=len(events))


def _command_subject(text: str, now: datetime) -> tuple[str, date | None]:
    """Remove calendar command/date scaffolding before title matching."""
    date_hint = _date_from_text(text, now)
    subject = re.sub(r"\b(?:cancel|delete|remove|is|are)\b", " ", text, flags=re.IGNORECASE)
    subject = re.sub(r"\b(?:the\s+)?(?:personal\s+)?calendar\b", " ", subject, flags=re.IGNORECASE)
    subject = re.sub(r"\b(?:the\s+)?event\b", " ", subject, flags=re.IGNORECASE)
    subject = _EXPLICIT_DATE.sub(" ", subject)
    subject = re.sub(r"\b(?:today|tomorrow|this|next)\s*(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)?\b", " ", subject, flags=re.IGNORECASE)
    subject = re.sub(r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", " ", subject, flags=re.IGNORECASE)
    subject = re.sub(r"\b(?:at|from|to|for|on)\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b", " ", subject, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", subject).strip(" .,:"), date_hint


def _confirmed_followup_event() -> dict[str, object] | None:
    """Resolve the last conversational reference against Google before use."""
    if not _last_confirmed_event_id:
        return None
    adapter = _google_calendar()
    if not adapter:
        return None
    result = adapter.find_event(_last_confirmed_event_id)
    if not result.success:
        _log("google_find", success=False)
        return None
    _log("google_find", success=True)
    return result.value


def is_ambiguous_event_followup(user_text: str) -> bool:
    """Whether text asks to inspect or select current ambiguity candidates."""
    text = user_text.strip()
    return (_AMBIGUOUS_EVENT_LIST_PATTERN.fullmatch(text) is not None or
            _AMBIGUOUS_EVENT_ORDINAL_PATTERN.fullmatch(text) is not None or
            bool(re.search(r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}(?:st|nd|rd|th)?\s+(?:one|event)\b", text, re.IGNORECASE)))


def _ambiguous_candidates() -> list[dict[str, object]]:
    """Re-read current candidates and discard IDs no longer in Google."""
    global _ambiguous_event_ids
    adapter = _google_calendar()
    if not adapter or not _ambiguous_event_ids:
        return []
    events: list[dict[str, object]] = []
    for event_id in _ambiguous_event_ids:
        result = adapter.find_event(event_id)
        _log("google_find", success=result.success)
        if result.success and isinstance(result.value, dict):
            events.append(result.value)
    _ambiguous_event_ids = tuple(str(event["id"]) for event in events if event.get("id"))
    return events


def _candidate_line(event: dict[str, object]) -> str:
    title = str(event.get("title") or "(untitled event)")
    try:
        start = datetime.fromisoformat(str(event["start"])).astimezone(_zone())
        date_text = start.strftime("%B %d, %Y").replace(" 0", " ")
        timing = "all-day" if event.get("all_day") else _display_time(start)
    except (KeyError, ValueError):
        date_text, timing = "unknown date", "unknown time"
    location = str(event.get("location") or "").strip()
    return f"- {title} — {date_text} — {timing}" + (f" — {location}" if location else "")


def _ambiguous_event_response(user_text: str, now: datetime) -> str:
    events = _ambiguous_candidates()
    if not events:
        _clear_ambiguous_event_reference()
        return "Which calendar event do you mean?"
    text = user_text.strip()
    if _AMBIGUOUS_EVENT_LIST_PATTERN.fullmatch(text):
        return f"There are {len(events)} matching events:\n\n" + "\n".join(_candidate_line(event) for event in events) + "\n\nWhich one do you mean?"
    ordinal = _AMBIGUOUS_EVENT_ORDINAL_PATTERN.fullmatch(text)
    if ordinal:
        positions = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2, "fourth": 3, "4th": 3, "fifth": 4, "5th": 4}
        position = positions[ordinal.group("ordinal").lower()]
        if position < len(events):
            _remember_confirmed_event(events[position])
            return f"Selected {_candidate_line(events[position])[2:]}"
        return f"There are only {len(events)} matching events. Which one do you mean?"
    date_hint = _date_from_text(text, now)
    matching = [event for event in events if date_hint and str(event.get("start", ""))[:10] == date_hint.isoformat()]
    if len(matching) == 1:
        _remember_confirmed_event(matching[0])
        return f"Selected {_candidate_line(matching[0])[2:]}"
    return "I need a more specific event selection."


def is_event_detail_followup(user_text: str) -> bool:
    """Whether text is a terse request about the active calendar event."""
    return _EVENT_DETAIL_FOLLOWUP_PATTERN.fullmatch(user_text.strip()) is not None


def _event_followup_detail_response(user_text: str) -> str:
    """Answer a terse event question from a fresh authoritative Google read."""
    event = _confirmed_followup_event()
    if event is None:
        return "Which calendar event do you mean?"
    title = str(event.get("title") or "That event")
    text = user_text.strip().lower().rstrip("?.!")
    all_day = bool(event.get("all_day"))
    try:
        start = datetime.fromisoformat(str(event["start"])).astimezone(_zone())
    except (KeyError, ValueError):
        return "I couldn't read that event's timing from your personal Google Calendar."
    day = start.strftime("%A, %B %d, %Y").replace(" 0", " ")
    if text == "what day":
        return f"{title} is on {day}."
    if text in {"where", "what's the location", "what is the location"}:
        location = str(event.get("location") or "").strip()
        return f"{title} is at {location}." if location else f"I don't have a location for {title} on your personal Google Calendar."
    if text.startswith("who is"):
        description = str(event.get("description") or "").strip()
        return f"The event details say: {description}" if description else f"I don't have attendee information for {title} on your personal Google Calendar."
    if all_day:
        return f"{title} is an all-day event on {day}."
    if text == "what time":
        return f"{title} starts at {_display_time(start)} on {day}."
    return f"{title} is on {day} at {_display_time(start)}."


def _display_time(value: datetime) -> str:
    return value.strftime("%I:%M %p").lstrip("0")


def _add_months(value: date, months: int) -> date:
    month = value.month - 1 + months
    year, month = value.year + month // 12, month % 12 + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def _weekend_events_response(text: str, now: datetime) -> str:
    """Return a concise, deduplicated Google Calendar weekend view."""
    months = re.search(r"\bnext\s+(?:(\d+)|(?:one|a)|two)\s+months?\b", text, re.IGNORECASE)
    if months:
        count = int(months.group(1)) if months.group(1) else 2
        start = datetime.combine(now.date(), time.min, tzinfo=_zone())
        end = datetime.combine(_add_months(now.date(), count), time.min, tzinfo=_zone())
        heading = (f"Weekend events through {(end - timedelta(days=1)).date():%B} "
                   f"{(end - timedelta(days=1)).day} "
                   f"(interpreting ‘next {count} months’ from today):")
    else:
        days_until_saturday = (5 - now.weekday()) % 7
        weekend_start = now.date() + timedelta(days=days_until_saturday)
        start = datetime.combine(weekend_start, time.min, tzinfo=_zone())
        end = start + timedelta(days=2)
        heading = (f"This weekend ({weekend_start:%A, %B} {weekend_start.day} "
                   f"through {(weekend_start + timedelta(days=1)):%A, %B} "
                   f"{(weekend_start + timedelta(days=1)).day}):")
    seen: set[tuple[str, date]] = set()
    grouped: dict[date, list[dict[str, object]]] = {}
    for event in get_personal_calendar_events(start, end):
        event_id = str(event.get("id") or f"{event.get('title')}|{event.get('start')}|{event.get('end')}")
        try:
            event_start = date.fromisoformat(str(event["start"])[:10])
            event_end = date.fromisoformat(str(event["end"])[:10])
        except (KeyError, ValueError):
            continue
        # Google all-day event ends are exclusive.  Timed events ending at
        # midnight on a later date also should not occupy that following day.
        if event_end > event_start and str(event["end"])[11:16] in ("", "00:00"):
            event_end -= timedelta(days=1)
        cursor = max(event_start, start.date())
        final_date = min(event_end, (end - timedelta(days=1)).date())
        while cursor <= final_date:
            identity = (event_id, cursor)
            if cursor.weekday() >= 5 and identity not in seen:
                seen.add(identity)
                grouped.setdefault(cursor, []).append(event)
            cursor += timedelta(days=1)
    if not grouped:
        return "No personal Google Calendar events fall during that weekend range."
    lines = [heading]
    for event_date in sorted(grouped):
        lines.append(f"{event_date:%A, %B} {event_date.day}:")
        for event in grouped[event_date]:
            suffix = ""
            if not bool(event.get("all_day")):
                try:
                    suffix = f" at {_display_time(datetime.fromisoformat(str(event['start'])).astimezone(_zone()))}"
                except ValueError:
                    pass
            lines.append(f"- {event.get('title', '(untitled event)')}{suffix}")
    return "\n".join(lines)


def _is_definite_commitment(text: str) -> bool:
    if "?" in text or _has_tentative_language(text) or _has_historical_language(text):
        return False
    return bool(re.search(
        r"\b(?:i(?:'m| am)\s+going|taking\s+pto|[a-z][a-z'-]*\s+is\s+coming|"
        r"i\s+have\s+(?:an?\s+)?(?:doctor'?s?\s+)?appointment|appointment|"
        r"company\s+retreat|alumni\s+weekend|\w+\s+game|\bpto\b|dominican|zach\s+bryan|"
        r"\w+\s+arrives?|\w+\s+leaves?|doctor\s+appointment)\b",
        text,
        re.IGNORECASE,
    ))


def _event_title(statement: str) -> str:
    title = _EXPLICIT_DATE.sub("", statement)
    title = re.sub(r"^\s*(?:-|to|through)\s*\d{1,2}(?:st|nd|rd|th)?\s*", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\bto\s+(?:the\s+)?\d{1,2}(?:st|nd|rd|th)?\b", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\b(?:this|next)\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\b(?:on\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\bleaving\s+.*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\b(?:i'm|i am)\s+(?:taking\s+)?", "", title, flags=re.IGNORECASE)
    title = re.sub(r"^\s*going\s+to\s+", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+(?:in\s+the\s+)?morning\b", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+", " ", title).strip(" ,.-:")
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
    # Newlines and explicit commitment clauses are event boundaries in batches.
    statements = [part.strip(" \t.-") for part in re.split(r"[\r\n]+", user_text) if part.strip()]
    if len(statements) == 1:
        statements = [part.strip(" \t.-") for part in re.split(r"(?<=[.!])\s+|-\s*(?=i\s+have)", user_text, flags=re.IGNORECASE) if part.strip()]
    else:
        statements = [part.strip(" \t.-") for statement in statements for part in re.split(r"-\s*(?=i\s+have)", statement, flags=re.IGNORECASE) if part.strip()]
    for statement in statements:
        if not _is_definite_commitment(statement):
            continue
        dates = _date_range_from_statement(statement, current)
        if dates is None:
            continue
        start_date, last_date = dates
        start_time = _plan_time(statement)
        all_day = start_time is None
        start = datetime.combine(start_date, start_time or time.min, tzinfo=zone)
        # Timed commitments default to one hour. All-day end is exclusive.
        end = (start + timedelta(hours=1)) if not all_day else datetime.combine(last_date + timedelta(days=1), time.min, tzinfo=zone)
        candidates.append(CalendarEventCandidate(_event_title(statement), start, end, all_day, statement))
        if "alumni weekend" in statement.lower() and last_date:
            weekend_start = last_date + timedelta(days=(5 - last_date.weekday()) % 7 or 7)
            weekend_end = weekend_start + timedelta(days=2)
            candidates.append(CalendarEventCandidate("Holy Cross Alumni Weekend", datetime.combine(weekend_start, time.min, tzinfo=zone), datetime.combine(weekend_end, time.min, tzinfo=zone), True, statement))
    return candidates


def _create_definite_event_candidates(user_text: str, now: datetime) -> str | None:
    candidates = extract_definite_event_candidates(user_text, now)
    if not candidates:
        return None
    created: list[str] = []
    existing: list[str] = []
    failed: list[str] = []
    for candidate in candidates:
        try:
            matches = _find_events(candidate.title, now, exact=True, date_hint=candidate.start.date())
            if matches:
                existing.append(candidate.title)
                continue
            event = create_personal_calendar_event(candidate.title, candidate.start, candidate.end, all_day=candidate.all_day)
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
    if existing:
        messages.append("Already on your personal Google Calendar: " + "; ".join(existing) + ".")
    return " ".join(messages)


def handle_personal_calendar_request(user_text: str, now: datetime | None = None) -> str:
    """Handle explicit personal-calendar commands without an LLM decision."""
    zone = _zone()
    current = (now or datetime.now(zone)).astimezone(zone)
    text = user_text.strip().rstrip("?.!")
    lowered = text.lower()
    if _has_tentative_language(text) and re.search(r"\b(?:add|schedule|put|create|book)\b", lowered):
        return "Please specify a definite calendar commitment before I add it."
    if (_has_tentative_language(text) or _has_historical_language(text)) and re.search(r"\b(?:actually|instead|now|move|reschedule|change|update)\b", lowered):
        return "I didn't change the calendar because that sounded tentative or historical."
    if re.match(r"^\s*(?:is|are)\b", lowered) and re.search(r"\b(?:on|in)\s+(?:my\s+)?(?:personal\s+)?calendar\b", lowered):
        subject, date_hint = _command_subject(text, current)
        matches = _find_events(subject, current, partial=True, date_hint=date_hint) if subject else []
        if len(matches) == 1:
            _remember_confirmed_event(matches[0])
            return "It is currently on your personal Google Calendar."
        if len(matches) > 1:
            _remember_ambiguous_events(matches)
            return (f"There are {len(matches)} matching events:\n\n" +
                    "\n".join(_candidate_line(event) for event in matches) +
                    "\n\nWhich one do you mean?")
        _clear_active_event_reference()
        _clear_ambiguous_event_reference()
        return "It is not currently on your personal Google Calendar."
    if is_ambiguous_event_followup(text):
        return _ambiguous_event_response(text, current)
    if is_event_detail_followup(text):
        return _event_followup_detail_response(text)
    if has_ambiguous_event_reference():
        _clear_ambiguous_event_reference()
    if re.search(r"\b(?:cancel|delete|remove)\b|\b(?:isn't|is not|aren't|are not)\b.*\b(?:anymore|off|cancel)|\b(?:is|are)\b.*\b(?:off|cancelled|canceled|anymore)", lowered):
        is_followup = bool(re.search(r"\b(?:that|it|this)\s+(?:event|one)\b|^\s*(?:cancel|delete|remove)\s+(?:that|it|this)\b", lowered))
        confirmed = _confirmed_followup_event() if is_followup else None
        subject, date_hint = _command_subject(text, current)
        matches = [confirmed] if confirmed else (_find_events(subject, current, partial=True, date_hint=date_hint) if subject else [])
        if len(matches) != 1:
            return "I need the exact personal-calendar event to delete." if not matches else "I found more than one matching event. Which one should I delete?"
        if not delete_personal_calendar_event(matches[0]["id"]):
            return "I couldn't delete that event from your personal Google Calendar."
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
            updated = update_personal_calendar_event(event["id"], start=moved_start.isoformat(), end=(moved_start + (end - start)).isoformat())
            if updated is None:
                return "I couldn't update that event on your personal Google Calendar."
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
        updated = update_personal_calendar_event(event["id"], start=moved_start.isoformat(), end=moved_end.isoformat())
        if updated is None:
            return "I couldn't update that event on your personal Google Calendar."
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
                event["id"],
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
        if "weekend" in lowered:
            return _weekend_events_response(text, current)
        named = re.search(r"\bwhen\s+(?:is|am i going to)\s+(.+?)(?:\?|$)", text, re.IGNORECASE)
        if named:
            matches = _find_events(named.group(1).strip(), current, partial=True)
            _log_lookup(matches)
            return "No personal-calendar events found." if not matches else "Personal calendar:\n" + "\n".join(_event_line(event) for event in matches)
        # A leading "Do I have ..." is an existence query.  Do not mistake
        # "What do I have tomorrow?" for one and discard its date range.
        existing = re.search(r"^\s*do\s+i\s+have\s+(?:an?\s+)?(.+?)(?:\?|$)", text, re.IGNORECASE)
        if existing:
            subject, date_hint = _command_subject(existing.group(1), current)
            matches = _find_events(subject, current, partial=True, date_hint=date_hint) if subject else []
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
    # Treat calendar wording as command scaffolding only when it names the
    # user's target calendar.  A title such as "Sheila Calendar Test" must
    # not turn the entire utterance into a title-bearing command.
    explicit_create = bool(re.search(
        r"\b(?:to\s+)?(?:my\s+|personal\s+)(?:google\s+)?calendar\b|\b(?:add|schedule|put|create|book)\s+to\s+calendar\b",
        lowered,
    ))
    if not explicit_create:
        committed = _create_definite_event_candidates(text, current)
        if committed is not None:
            return committed
    if re.search(r"\b(?:add|schedule|put|create|book)\b", lowered):
        # Remove a leading action whether or not the user also named the
        # destination calendar.  A trailing "add to my calendar" remains
        # available for the cleanup below, while "Add Sam birthday ..." does
        # not leak the verb into the persisted title.
        command = re.sub(r"^\s*\b(?:add|schedule|put|create|book)\s+(?:an?\s+)?", "", text, flags=re.IGNORECASE)
        time_range = _time_range_from_text(command)
        start_time = time_range[0] if time_range else _time_from_text(command)
        event_date = _date_from_text(command, current)
        if not event_date:
            return "Please specify the event date before I add it."
        called_title = re.search(r"\bcalled\s+(.+?)(?=\s+\d{1,2}(?::\d{2})?\s*-\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)\b|$)", command, re.IGNORECASE)
        location_match = re.search(r"\bat\s+(.+?)(?=\s+\b(?:add|schedule|put|create|book)\b\s+(?:to\s+)?(?:my\s+)?(?:personal\s+)?calendar\b|$)", command, re.IGNORECASE)
        location = location_match.group(1).strip(" ,.") if location_match and not _time_from_text(location_match.group(1)) else ""
        title = called_title.group(1).strip(" ,") if called_title else re.split(r"\b(?:today|tomorrow|on|for)\s+(?:(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|(?:january|february|march|april|may|june|july|august|september|october|november|december)|\d)|at\s+\d", command, maxsplit=1, flags=re.IGNORECASE)[0].strip(" ,")
        title = _EXPLICIT_DATE.sub("", title)
        if location:
            title = re.sub(r"\bat\s+" + re.escape(location) + r"\b", "", title, flags=re.IGNORECASE)
        title = re.sub(r"\b(?:add|schedule|put|create|book)\s+(?:to\s+)?(?:my\s+)?(?:personal\s+)?calendar\b", "", title, flags=re.IGNORECASE)
        title = re.sub(r"\s+to\s+(?:my\s+)?personal\s+calendar\b", "", title, flags=re.IGNORECASE).strip(" ,")
        title = re.sub(r"\b(?:today|tomorrow)\b", "", title, flags=re.IGNORECASE).strip(" ,")
        title = _EXPLICIT_DATE.sub("", title)
        title = re.sub(r"\bas\s+(?:an?\s+)?all[ -]?day\s+(?:personal\s+)?google\s+calendar\s+event\b", "", title, flags=re.IGNORECASE).strip(" ,")
        if not title:
            return "Please give the event a title before I add it."
        all_day = start_time is None
        start = datetime.combine(event_date, start_time or time.min, tzinfo=zone)
        end = (datetime.combine(event_date, time_range[1], tzinfo=zone) if time_range
               else (datetime.combine(event_date + timedelta(days=1), time.min, tzinfo=zone) if all_day else start + timedelta(hours=1)))
        if end <= start:
            end += timedelta(days=1)
        if _find_events(title, current, exact=True, date_hint=event_date):
            return f"{title} is already on your personal Google Calendar."
        event = create_personal_calendar_event(title, start, end, location=location, all_day=all_day)
        if all_day:
            return f"Added {event['title']} to your personal calendar for {start:%Y-%m-%d} as an all-day event."
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
                updated = update_personal_calendar_event(event["id"], start=start.isoformat(), end=(start + (old_end - old_start)).isoformat())
                if updated is None:
                    return "I couldn't update that event on your personal Google Calendar."
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
