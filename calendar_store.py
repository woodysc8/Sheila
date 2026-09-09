"""Persistent personal-calendar storage, separate from Google Calendar."""

from datetime import datetime
import sqlite3
from zoneinfo import ZoneInfo

import config


class CalendarError(ValueError):
    """Raised when a personal-calendar operation is invalid."""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS calendar_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            start_at TEXT NOT NULL,
            end_at TEXT NOT NULL,
            timezone TEXT NOT NULL,
            location TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    conn.commit()
    return conn


def _timezone(name: str | None = None) -> ZoneInfo:
    timezone_name = name or config.SHEILA_TIMEZONE
    try:
        return ZoneInfo(timezone_name)
    except (KeyError, ValueError) as exc:
        raise CalendarError(f"Unknown IANA timezone: {timezone_name}") from exc


def parse_datetime(value: str, timezone_name: str | None = None) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise CalendarError("Datetime is required.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CalendarError("Datetime must be ISO 8601.") from exc
    if parsed.tzinfo is None:
        if not timezone_name:
            raise CalendarError("Datetime must include timezone information.")
        parsed = parsed.replace(tzinfo=_timezone(timezone_name))
    return parsed.astimezone(_timezone(timezone_name))


def _event(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "start": row["start_at"],
        "end": row["end_at"],
        "timezone": row["timezone"],
        "location": row["location"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_event(title: str, start: str | datetime, end: str | datetime,
                 description: str = "", timezone: str | None = None, location: str = "") -> dict[str, object]:
    if not isinstance(title, str) or not title.strip():
        raise CalendarError("Event title is required.")
    timezone_name = timezone or config.SHEILA_TIMEZONE
    zone = _timezone(timezone_name)
    start_at = start if isinstance(start, datetime) else parse_datetime(start, timezone_name)
    end_at = end if isinstance(end, datetime) else parse_datetime(end, timezone_name)
    if start_at.tzinfo is None or end_at.tzinfo is None:
        raise CalendarError("Event datetimes must be timezone-aware.")
    start_at, end_at = start_at.astimezone(zone), end_at.astimezone(zone)
    if end_at <= start_at:
        raise CalendarError("Event end must be after its start.")
    now = datetime.now(zone).isoformat()
    conn = _connect()
    cursor = conn.execute(
        """INSERT INTO calendar_events
           (title, description, start_at, end_at, timezone, location, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (title.strip(), description or "", start_at.isoformat(), end_at.isoformat(), timezone_name,
         location or "", now, now),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM calendar_events WHERE id = ?", (cursor.lastrowid,)).fetchone()
    conn.close()
    return _event(row)


def get_event(event_id: int) -> dict[str, object] | None:
    conn = _connect()
    row = conn.execute("SELECT * FROM calendar_events WHERE id = ?", (event_id,)).fetchone()
    conn.close()
    return _event(row) if row else None


def list_events(start: str | datetime, end: str | datetime, timezone: str | None = None) -> list[dict[str, object]]:
    timezone_name = timezone or config.SHEILA_TIMEZONE
    start_at = start if isinstance(start, datetime) else parse_datetime(start, timezone_name)
    end_at = end if isinstance(end, datetime) else parse_datetime(end, timezone_name)
    if end_at <= start_at:
        raise CalendarError("Range end must be after its start.")
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM calendar_events WHERE julianday(start_at) < julianday(?) AND julianday(end_at) > julianday(?) ORDER BY julianday(start_at), id",
        (end_at.astimezone(_timezone(timezone_name)).isoformat(), start_at.astimezone(_timezone(timezone_name)).isoformat()),
    ).fetchall()
    conn.close()
    return [_event(row) for row in rows]


def update_event(event_id: int, **changes: object) -> dict[str, object] | None:
    existing = get_event(event_id)
    if existing is None:
        return None
    allowed = {"title", "description", "start", "end", "timezone", "location"}
    unknown = set(changes) - allowed
    if unknown:
        raise CalendarError(f"Unsupported event fields: {', '.join(sorted(unknown))}")
    timezone_name = str(changes.get("timezone") or existing["timezone"])
    _timezone(timezone_name)
    title = changes.get("title", existing["title"])
    description = changes.get("description", existing["description"]) or ""
    location = changes.get("location", existing["location"]) or ""
    if not isinstance(title, str) or not title.strip():
        raise CalendarError("Event title is required.")
    start = parse_datetime(str(changes.get("start", existing["start"])), timezone_name)
    end = parse_datetime(str(changes.get("end", existing["end"])), timezone_name)
    if end <= start:
        raise CalendarError("Event end must be after its start.")
    now = datetime.now(_timezone(timezone_name)).isoformat()
    conn = _connect()
    conn.execute(
        """UPDATE calendar_events SET title = ?, description = ?, start_at = ?, end_at = ?,
           timezone = ?, location = ?, updated_at = ? WHERE id = ?""",
        (title.strip(), description, start.isoformat(), end.isoformat(), timezone_name, location, now, event_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM calendar_events WHERE id = ?", (event_id,)).fetchone()
    conn.close()
    return _event(row)


def delete_event(event_id: int) -> bool:
    conn = _connect()
    cursor = conn.execute("DELETE FROM calendar_events WHERE id = ?", (event_id,))
    conn.commit()
    conn.close()
    return cursor.rowcount > 0