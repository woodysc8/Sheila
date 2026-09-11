"""Sheila-owned persistent reminders and task commands."""

from datetime import date, datetime, time, timedelta
from calendar import monthrange
import os
import re
import sqlite3
from zoneinfo import ZoneInfo

import config
import operational_store
import personal_calendar

STATUSES = {"pending", "completed", "cancelled"}
_RELATIVE_REMINDER_PATTERN = re.compile(
    r"^\s*remind me\s+in\s+(?:(?P<article>an?|the)\s+|(?P<amount>\d+)\s+)"
    r"(?P<unit>seconds?|minutes?|hours?)\s+to\s+(?P<text>.+)$",
    re.IGNORECASE,
)
_RELATIVE_DURATION_PATTERN = re.compile(
    r"\bin\s+(?:(?P<article>an?|the)\s+|(?P<amount>\d+)\s+)"
    r"(?P<unit>seconds?|minutes?|hours?)\b",
    re.IGNORECASE,
)


def _zone() -> ZoneInfo:
    return config.get_sheila_timezone()


def _connect() -> sqlite3.Connection:
    path = os.path.abspath(config.DB_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS sheila_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            due_date TEXT,
            due_at TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    conn.commit()
    return conn


def _row(row: sqlite3.Row) -> dict[str, object]:
    return dict(row)


def _date_from_text(text: str, now: datetime) -> date | None:
    lowered = text.lower()
    lowered = re.sub(r"\btmw\b", "tomorrow", lowered)
    text = re.sub(r"\btmw\b", "tomorrow", text, flags=re.IGNORECASE)
    resolved = personal_calendar.resolve_calendar_date(text, now)
    if resolved is not None:
        return resolved
    if "today" in lowered:
        return now.date()
    if "tomorrow" in lowered:
        return now.date() + timedelta(days=1)
    weekdays = {name: index for index, name in enumerate(("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"))}
    for name, weekday in weekdays.items():
        if re.search(rf"\b{name}\b", lowered):
            return now.date() + timedelta(days=(weekday - now.weekday()) % 7 or 7)
    return None


def _time_from_text(text: str) -> time | None:
    match = re.search(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm|in\s+the\s+morning)?\b", text, re.IGNORECASE)
    if not match:
        return time(9, 0) if re.search(r"\bmorning\b", text, re.IGNORECASE) else None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = (match.group(3) or "pm").lower()
    if "morning" in meridiem:
        meridiem = "am"
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def _parse_due(text: str, now: datetime) -> tuple[str | None, str | None]:
    relative = _relative_due(text, now)
    if relative is not None:
        return relative.date().isoformat(), relative.isoformat()
    event_date = _date_from_text(text, now)
    if event_date is None:
        return None, None
    event_time = _time_from_text(text)
    due_at = datetime.combine(event_date, event_time, tzinfo=_zone()).isoformat() if event_time else None
    return event_date.isoformat(), due_at


def _relative_due(text: str, now: datetime) -> datetime | None:
    """Resolve ``in N minutes/hours`` without involving the model."""
    match = _RELATIVE_DURATION_PATTERN.search(text)
    if not match:
        return None
    amount = 1 if match.group("article") else int(match.group("amount"))
    unit = match.group("unit").lower()
    if unit.startswith("second"):
        delta = timedelta(seconds=amount)
    elif unit.startswith("minute"):
        delta = timedelta(minutes=amount)
    else:
        delta = timedelta(hours=amount)
    return now + delta


def _reminder_text(body: str) -> str:
    """Remove only scheduling clauses, preserving the requested action."""
    value = re.sub(r"\bon\s+the\s+\d{1,2}(?:st|nd|rd|th)?(?:\s+and\s+\d{1,2}(?:st|nd|rd|th)?)?\s+every\s+month\b", "", body, flags=re.IGNORECASE)
    value = re.sub(r"\b(?:today|tomorrow|tmw)\b", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(?:this|next)\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(?:on\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm|in\s+the\s+morning)\b", "", value, flags=re.IGNORECASE)
    value = _RELATIVE_DURATION_PATTERN.sub("", value)
    return re.sub(r"\s+", " ", value).strip(" ,.-")


def _next_monthly_due(day: int, current: datetime) -> datetime:
    """Keep day 29/30/31 intact; skip months that cannot contain it."""
    year, month = current.year, current.month
    while True:
        if day <= monthrange(year, month)[1]:
            candidate = current.replace(year=year, month=month, day=day, hour=9, minute=0, second=0, microsecond=0)
            if candidate >= current:
                return candidate
        month += 1
        if month == 13:
            year, month = year + 1, 1


def _default_due(current: datetime) -> datetime:
    """Schedule an unscheduled reminder two hours out, avoiding overnight delivery."""
    # Early-morning requests are deferred to that day's convenient afternoon slot.
    # This preserves the 5:59 AM boundary in the product rule.
    if current.time() < time(6):
        return datetime.combine(current.date(), time(13), tzinfo=_zone())
    candidate = current + timedelta(hours=2)
    if candidate.time() >= time(20, 30):
        return datetime.combine(candidate.date() + timedelta(days=1), time(13), tzinfo=_zone())
    if candidate.time() < time(6):
        return datetime.combine(candidate.date(), time(13), tzinfo=_zone())
    return candidate


def _next_time_due(reminder_time: time, current: datetime) -> datetime:
    """Return the next local occurrence of an explicitly supplied clock time."""
    candidate = datetime.combine(current.date(), reminder_time, tzinfo=_zone())
    return candidate if candidate >= current else candidate + timedelta(days=1)


def _has_explicit_time(text: str) -> bool:
    """Avoid treating unrelated numbers in reminder text as a time of day."""
    return bool(re.search(
        r"\bat\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b|"
        r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b|\bin\s+the\s+morning\b",
        text,
        re.IGNORECASE,
    ))


def _due_day_label(due: datetime, current: datetime) -> str:
    if due.date() == current.date():
        return "today"
    if due.date() == current.date() + timedelta(days=1):
        return "tomorrow"
    return f"on {due.strftime('%B')} {due.day}"


def _default_confirmation(due: datetime, day: str) -> str:
    human_time = due.strftime("%I:%M %p").lstrip("0")
    return f"Got it. I'll remind you {day} at {human_time}."


def _operational_create(text: str, current: datetime) -> str | None:
    """Create a durable reminder or retain deterministic missing-time state."""
    relative = _RELATIVE_REMINDER_PATTERN.match(text)
    if relative:
        due = _relative_due(text, current)
        reminder_text = relative.group("text").strip(" ,.-")
        if not reminder_text:
            return "Please specify what I should remind you about."
        operational_store.create_reminder(reminder_text, due, config.SHEILA_TIMEZONE, user_id=config.SHEILA_USER_ID)
        return f"Reminder set: {reminder_text}."
    match = re.search(r"\bremind me\s+(?:to|about)\s+(.+)$", text, re.IGNORECASE)
    if not match:
        pending = operational_store.take_pending(config.SHEILA_USER_ID, current)
        if not pending:
            return None
        reminder_time = _time_from_text(text)
        if reminder_time is None:
            operational_store.save_pending(config.SHEILA_USER_ID, pending["text"], pending["due_date"], pending["timezone"], current + timedelta(hours=24))
            return "Please specify a reminder time."
        due = datetime.combine(date.fromisoformat(pending["due_date"]), reminder_time, tzinfo=_zone())
        operational_store.create_reminder(pending["text"], due, pending["timezone"], user_id=config.SHEILA_USER_ID)
        return f"Reminder set: {pending['text']}."
    body = match.group(1)
    reminder_text = _reminder_text(body)
    monthly = re.search(r"\bon\s+the\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s+and\s+(\d{1,2})(?:st|nd|rd|th)?)?\s+every\s+month\b", body, re.IGNORECASE)
    if monthly:
        created = []
        for raw_day in monthly.groups():
            if raw_day:
                day = int(raw_day); due = _next_monthly_due(day, current)
                operational_store.create_reminder(reminder_text or "Monthly reminder", due, config.SHEILA_TIMEZONE, {"type":"monthly_day","day":day}, config.SHEILA_USER_ID); created.append(str(day))
        return "Reminder set: monthly on " + " and ".join(created) + "."
    if not reminder_text:
        return "Please specify what I should remind you about."
    due_date, due_at = _parse_due(body, current)
    if due_date is None:
        if _has_explicit_time(body):
            reminder_time = _time_from_text(body)
            if reminder_time is None:
                return "Please specify when I should remind you."
            due = _next_time_due(reminder_time, current)
            operational_store.create_reminder(reminder_text, due, config.SHEILA_TIMEZONE, user_id=config.SHEILA_USER_ID)
            return _default_confirmation(due, _due_day_label(due, current))
        due = _default_due(current)
        operational_store.create_reminder(reminder_text, due, config.SHEILA_TIMEZONE, user_id=config.SHEILA_USER_ID)
        return _default_confirmation(due, _due_day_label(due, current))
    if due_at is None:
        due = datetime.combine(date.fromisoformat(due_date), time(9), tzinfo=_zone())
        operational_store.create_reminder(reminder_text, due, config.SHEILA_TIMEZONE, user_id=config.SHEILA_USER_ID)
        return _default_confirmation(due, _due_day_label(due, current))
    operational_store.create_reminder(reminder_text, datetime.fromisoformat(due_at), config.SHEILA_TIMEZONE, user_id=config.SHEILA_USER_ID)
    return f"Reminder set: {reminder_text}."


def create(text: str, due_date: str | None, due_at: str | None) -> dict[str, object]:
    if not text.strip():
        raise ValueError("Reminder text is required.")
    now = datetime.now(_zone()).isoformat()
    conn = _connect()
    cursor = conn.execute(
        "INSERT INTO sheila_tasks (text, due_date, due_at, status, created_at, updated_at) VALUES (?, ?, ?, 'pending', ?, ?)",
        (text.strip(), due_date, due_at, now, now),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM sheila_tasks WHERE id = ?", (cursor.lastrowid,)).fetchone()
    conn.close()
    return _row(row)


def list_tasks(status: str = "pending", due_date: str | None = None) -> list[dict[str, object]]:
    conn = _connect()
    query = "SELECT * FROM sheila_tasks WHERE status = ?"
    params: list[object] = [status]
    if due_date:
        query += " AND due_date = ?"
        params.append(due_date)
    query += " ORDER BY COALESCE(due_at, due_date), id"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [_row(row) for row in rows]


def update(task_id: int, *, text: str | None = None, due_date: str | None = None, due_at: str | None = None, replace_due_at: bool = False) -> dict[str, object] | None:
    existing = next((task for task in list_tasks(status="pending") if task["id"] == task_id), None)
    if existing is None:
        return None
    now = datetime.now(_zone()).isoformat()
    conn = _connect()
    conn.execute(
        "UPDATE sheila_tasks SET text = ?, due_date = ?, due_at = ?, updated_at = ? WHERE id = ? AND status = 'pending'",
        (text if text is not None else existing["text"], due_date if due_date is not None else existing["due_date"], due_at if replace_due_at else (due_at if due_at is not None else existing["due_at"]), now, task_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM sheila_tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    return _row(row) if row else None


def set_status(task_id: int, status: str) -> dict[str, object] | None:
    if status not in STATUSES:
        raise ValueError(f"Unknown task status: {status}")
    conn = _connect()
    conn.execute("UPDATE sheila_tasks SET status = ?, updated_at = ? WHERE id = ?", (status, datetime.now(_zone()).isoformat(), task_id))
    conn.commit()
    row = conn.execute("SELECT * FROM sheila_tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    return _row(row) if row else None


def _format_task(task: dict[str, object]) -> str:
    due = task["due_at"] or task["due_date"] or "unscheduled"
    return f"- {task['text']} ({due})"


def handle_request(user_text: str, now: datetime | None = None) -> str:
    current = (now or datetime.now(_zone())).astimezone(_zone())
    text = user_text.strip().rstrip("?.!")
    lowered = text.lower()
    operational_store.require_configured()
    if re.search(r"\b(?:maybe|might|may|should i|thinking about|if)\b", lowered):
        return "I didn't create a reminder because that sounded tentative."
    if operational_store.configured():
        active = operational_store.list_reminders(config.SHEILA_USER_ID)
        cancel_match = re.search(r"cancel\s+(?:my\s+)?(?:reminder|task)\s+(?:to\s+)?(.+)$", text, re.IGNORECASE)
        complete_match = re.search(r"(?:mark|set)\s+(?:my\s+)?(?:reminder|task)\s+(.+?)\s+(?:done|complete|completed)$", text, re.IGNORECASE)
        update_match = re.search(r"(?:move|change|update)\s+(?:my\s+)?(?:reminder|task)\s+(?:to\s+)?(.+?)\s+to\s+(.+)$", text, re.IGNORECASE)
        if cancel_match or complete_match or update_match:
            match = cancel_match or complete_match or update_match; needle = match.group(1).strip().lower()
            matches = [item for item in active if needle in str(item["text"]).lower()]
            if len(matches) != 1:
                return "I need the exact reminder to update." if not matches else "I found more than one matching reminder."
            item = matches[0]
            if cancel_match:
                operational_store.set_reminder_status(item["id"], "cancelled"); return f"Cancelled reminder: {item['text']}."
            if complete_match:
                operational_store.set_reminder_status(item["id"], "completed"); return f"Completed reminder: {item['text']}."
            due_date, due_at = _parse_due(update_match.group(2), current)
            if due_date is None: return "Please specify the new reminder date."
            operational_store.update_reminder(item["id"], due_at=datetime.fromisoformat(due_at) if due_at else datetime.combine(date.fromisoformat(due_date), time(9), tzinfo=_zone()))
            return f"Updated reminder: {item['text']}."
        result = _operational_create(text, current)
        if result is not None:
            return result
        if re.search(r"\b(?:what are my reminders|list my reminders|show my reminders)\b", lowered):
            items = operational_store.list_reminders()
            return "No pending reminders." if not items else "Reminders:\n" + "\n".join(f"- {item['text']} ({item['due_at']})" for item in items)
        return "I couldn't identify a reminder request."
    if re.search(r"\b(?:maybe|might|may|should i|thinking about|if)\b", lowered):
        return "I didn't create a reminder because that sounded tentative."
    cancel_match = re.search(r"cancel\s+(?:my\s+)?(?:reminder|task)\s+(?:to\s+)?(.+)$", text, re.IGNORECASE)
    if cancel_match:
        needle = cancel_match.group(1).strip().lower()
        matches = [task for task in list_tasks() if needle in str(task["text"]).lower()]
        if len(matches) != 1:
            return "I need the exact reminder to cancel." if not matches else "I found more than one matching reminder."
        set_status(int(matches[0]["id"]), "cancelled")
        return f"Cancelled reminder: {matches[0]['text']}."
    update_match = re.search(r"(?:move|change|update)\s+(?:my\s+)?(?:reminder|task)\s+(?:to\s+)?(.+?)\s+to\s+(.+)$", text, re.IGNORECASE)
    if update_match:
        needle = update_match.group(1).strip().lower()
        matches = [task for task in list_tasks() if needle in str(task["text"]).lower()]
        if len(matches) != 1:
            return "I need the exact reminder to update."
        due_date, due_at = _parse_due(update_match.group(2), current)
        if due_date is None:
            return "Please specify the new reminder date."
        update(int(matches[0]["id"]), due_date=due_date, due_at=due_at, replace_due_at=True)
        return f"Updated reminder: {matches[0]['text']}."
    complete_match = re.search(r"(?:mark|set)\s+(?:my\s+)?(?:reminder|task)\s+(.+?)\s+(?:done|complete|completed)$", text, re.IGNORECASE)
    if complete_match:
        needle = complete_match.group(1).strip().lower()
        matches = [task for task in list_tasks() if needle in str(task["text"]).lower()]
        if len(matches) != 1:
            return "I need the exact reminder to complete."
        set_status(int(matches[0]["id"]), "completed")
        return f"Completed reminder: {matches[0]['text']}."
    if re.search(r"\b(?:what do i need to get done|what needs to get done|what are my reminders|list my reminders|show my reminders)\b", lowered):
        due_date = current.date().isoformat() if "today" in lowered else None
        tasks = list_tasks(due_date=due_date)
        return "No pending reminders." if not tasks else "Reminders:\n" + "\n".join(_format_task(task) for task in tasks)
    create_match = re.search(r"\bremind me to\s+(.+)$", text, re.IGNORECASE)
    if create_match:
        reminder_text = re.sub(r"\b(?:today|tomorrow|this|next|monday|tuesday|wednesday|thursday|friday|saturday|sunday|morning|at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b", "", create_match.group(1), flags=re.IGNORECASE)
        due_date, due_at = _parse_due(create_match.group(1), current)
        if due_date is None:
            return "Please specify when I should remind you."
        task = create(reminder_text.strip(" ,"), due_date, due_at)
        return f"Reminder set: {task['text']}."
    return "I couldn't identify a reminder request."
