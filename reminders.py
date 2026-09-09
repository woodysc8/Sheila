"""Stage 1 reminder records and conflict-aware evaluation hooks."""

from dataclasses import dataclass
from datetime import datetime, timedelta
import sqlite3
from typing import Callable

import config


STATUSES = {"pending", "sent", "cancelled"}


@dataclass(frozen=True)
class Reminder:
    id: int
    text: str
    due_at: datetime
    remind_at: datetime
    status: str
    created_at: datetime


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Reminder times must be timezone-aware.")
    return parsed.astimezone(config.get_sheila_timezone())


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, due_at TEXT NOT NULL,
        remind_at TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL
    )""")
    return conn


def create(text: str, due_at: datetime, remind_at: datetime | None = None, remind_before: timedelta | None = None) -> Reminder:
    if due_at.tzinfo is None or (remind_at and remind_at.tzinfo is None):
        raise ValueError("Reminder times must be timezone-aware.")
    zone = config.get_sheila_timezone()
    due_at = due_at.astimezone(zone)
    reminder_time = remind_at.astimezone(zone) if remind_at else due_at - (remind_before or timedelta(hours=1))
    created = datetime.now(zone)
    conn = _connect()
    cursor = conn.execute("INSERT INTO reminders (text, due_at, remind_at, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
                          (text.strip(), due_at.isoformat(), reminder_time.isoformat(), created.isoformat()))
    conn.commit()
    result = Reminder(cursor.lastrowid, text.strip(), due_at, reminder_time, "pending", created)
    conn.close()
    return result


def pending() -> list[Reminder]:
    conn = _connect()
    rows = conn.execute("SELECT * FROM reminders WHERE status = 'pending' ORDER BY remind_at").fetchall()
    conn.close()
    return [_from_row(row) for row in rows]


def set_status(reminder_id: int, status: str) -> None:
    if status not in STATUSES:
        raise ValueError(f"Unknown reminder status: {status}")
    conn = _connect()
    conn.execute("UPDATE reminders SET status = ? WHERE id = ?", (status, reminder_id))
    conn.commit()
    conn.close()


def due_candidates(now: datetime | None = None, calendar_conflicts: Callable[[datetime, datetime], bool] | None = None) -> list[Reminder]:
    """Return reminders ready for evaluation; sending is deliberately separate."""
    current = (now or datetime.now(config.get_sheila_timezone())).astimezone(config.get_sheila_timezone())
    conflicts = calendar_conflicts or (lambda _start, _end: False)
    return [item for item in pending() if item.remind_at <= current and not conflicts(current, item.due_at)]


def _from_row(row: sqlite3.Row) -> Reminder:
    return Reminder(row["id"], row["text"], _parse(row["due_at"]), _parse(row["remind_at"]), row["status"], _parse(row["created_at"]))