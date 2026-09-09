"""Sheila-owned persistent reminders and task commands."""

from datetime import date, datetime, time, timedelta
import os
import re
import sqlite3
from zoneinfo import ZoneInfo

import config

STATUSES = {"pending", "completed", "cancelled"}


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
    match = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", text, re.IGNORECASE)
    if not match:
        return time(9, 0) if re.search(r"\bmorning\b", text, re.IGNORECASE) else None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = (match.group(3) or "pm").lower()
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def _parse_due(text: str, now: datetime) -> tuple[str | None, str | None]:
    event_date = _date_from_text(text, now)
    if event_date is None:
        return None, None
    event_time = _time_from_text(text)
    due_at = datetime.combine(event_date, event_time, tzinfo=_zone()).isoformat() if event_time else None
    return event_date.isoformat(), due_at


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