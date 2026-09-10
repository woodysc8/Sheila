"""Durable operational state for Sheila (Postgres in production).

Memory remains in ``memory.py``.  ``sqlite://`` is accepted only as an
explicit test/development backend; production URLs must be PostgreSQL.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta
import json
import sqlite3

import config


class OperationalStoreError(RuntimeError):
    pass


def _url() -> str:
    return config.SHEILA_OPERATIONAL_DATABASE_URL


def configured() -> bool:
    return bool(_url())


def require_configured() -> None:
    """Fail closed on hosted production rather than silently using SQLite."""
    if config.SHEILA_REQUIRE_OPERATIONAL_DATABASE and not configured():
        raise OperationalStoreError("SHEILA_OPERATIONAL_DATABASE_URL is required for operational state.")


def _sqlite() -> bool:
    return _url().startswith("sqlite://")


@contextmanager
def connection():
    url = _url()
    if not url:
        raise OperationalStoreError("SHEILA_OPERATIONAL_DATABASE_URL is not configured.")
    if _sqlite():
        path = url.removeprefix("sqlite:///")
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return
    if not url.startswith(("postgres://", "postgresql://")):
        raise OperationalStoreError("Operational database URL must be PostgreSQL.")
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise OperationalStoreError("Install psycopg to use the operational database.") from exc
    conn = psycopg.connect(url, row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _sql(value: str) -> str:
    return value if _sqlite() else value.replace("?", "%s")


def _one(conn, sql: str, params=()):
    cur = conn.execute(_sql(sql), params)
    row = cur.fetchone()
    return dict(row) if row else None


def _all(conn, sql: str, params=()):
    return [dict(row) for row in conn.execute(_sql(sql), params).fetchall()]


def initialize() -> None:
    """Create only operational tables; this never reads or changes memory."""
    with connection() as conn:
        serial = "INTEGER PRIMARY KEY AUTOINCREMENT" if _sqlite() else "BIGSERIAL PRIMARY KEY"
        timestamp = "TEXT" if _sqlite() else "TIMESTAMPTZ"
        conn.execute(f"""CREATE TABLE IF NOT EXISTS calendar_events (
            id {serial}, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
            start_at {timestamp} NOT NULL, end_at {timestamp} NOT NULL, timezone TEXT NOT NULL,
            location TEXT NOT NULL DEFAULT '', created_at {timestamp} NOT NULL, updated_at {timestamp} NOT NULL)""")
        conn.execute(f"""CREATE TABLE IF NOT EXISTS reminders (
            id {serial}, user_id TEXT NOT NULL, text TEXT NOT NULL, due_at {timestamp} NOT NULL,
            timezone TEXT NOT NULL, recurrence TEXT, status TEXT NOT NULL,
            created_at {timestamp} NOT NULL, updated_at {timestamp} NOT NULL, last_sent_at {timestamp})""")
        conn.execute(f"""CREATE TABLE IF NOT EXISTS reminder_deliveries (
            id {serial}, reminder_id INTEGER NOT NULL, occurrence_at {timestamp} NOT NULL,
            status TEXT NOT NULL, attempt_count INTEGER NOT NULL DEFAULT 0,
            claimed_until {timestamp}, sent_at {timestamp}, last_error TEXT,
            UNIQUE(reminder_id, occurrence_at))""")
        conn.execute(f"""CREATE TABLE IF NOT EXISTS pending_reminder_clarifications (
            user_id TEXT PRIMARY KEY, text TEXT NOT NULL, due_date TEXT NOT NULL,
            timezone TEXT NOT NULL, created_at {timestamp} NOT NULL, expires_at {timestamp} NOT NULL)""")
        conn.execute(f"CREATE TABLE IF NOT EXISTS operational_migrations (source_key TEXT PRIMARY KEY, migrated_at {timestamp} NOT NULL)")


def migrated(source_key):
    initialize()
    with connection() as conn: return _one(conn, "SELECT source_key FROM operational_migrations WHERE source_key = ?", (source_key,)) is not None


def mark_migrated(source_key):
    with connection() as conn:
        conn.execute(_sql("INSERT INTO operational_migrations (source_key,migrated_at) VALUES (?,?) ON CONFLICT(source_key) DO NOTHING"), (source_key,datetime.now(config.get_sheila_timezone()).isoformat()))


def create_calendar_event(title, start, end, description="", timezone="America/New_York", location=""):
    initialize(); now = datetime.now(config.get_sheila_timezone()).isoformat()
    with connection() as conn:
        if _sqlite():
            cur = conn.execute(_sql("INSERT INTO calendar_events (title,description,start_at,end_at,timezone,location,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)"), (title,description,start.isoformat(),end.isoformat(),timezone,location,now,now))
            return _one(conn, "SELECT * FROM calendar_events WHERE id = ?", (cur.lastrowid,))
        return _one(conn, "INSERT INTO calendar_events (title,description,start_at,end_at,timezone,location,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?) RETURNING *", (title,description,start.isoformat(),end.isoformat(),timezone,location,now,now))


def list_calendar_events(start, end):
    initialize()
    with connection() as conn:
        return _all(conn, "SELECT * FROM calendar_events WHERE start_at < ? AND end_at > ? ORDER BY start_at, id", (end.isoformat(), start.isoformat()))


def get_calendar_event(event_id):
    initialize()
    with connection() as conn: return _one(conn, "SELECT * FROM calendar_events WHERE id = ?", (event_id,))


def update_calendar_event(event_id, values):
    initialize(); fields = {k: v for k, v in values.items() if k in {"title", "description", "start_at", "end_at", "timezone", "location"}}
    if not fields: return get_calendar_event(event_id)
    fields["updated_at"] = datetime.now(config.get_sheila_timezone()).isoformat()
    assignments = ", ".join(f"{key} = ?" for key in fields)
    with connection() as conn:
        conn.execute(_sql(f"UPDATE calendar_events SET {assignments} WHERE id = ?"), (*fields.values(), event_id))
        return _one(conn, "SELECT * FROM calendar_events WHERE id = ?", (event_id,))


def delete_calendar_event(event_id):
    initialize()
    with connection() as conn:
        cur = conn.execute(_sql("DELETE FROM calendar_events WHERE id = ?"), (event_id,))
        return cur.rowcount > 0


def create_reminder(text, due_at, timezone, recurrence=None, user_id=None):
    user_id = user_id or config.SHEILA_USER_ID
    initialize(); now = datetime.now(config.get_sheila_timezone()).isoformat(); recurrence_value = json.dumps(recurrence) if recurrence else None
    with connection() as conn:
        if _sqlite():
            cur = conn.execute(_sql("INSERT INTO reminders (user_id,text,due_at,timezone,recurrence,status,created_at,updated_at) VALUES (?,?,?,?,?,'active',?,?)"), (user_id,text,due_at.isoformat(),timezone,recurrence_value,now,now)); row = _one(conn, "SELECT * FROM reminders WHERE id = ?", (cur.lastrowid,))
        else:
            row = _one(conn, "INSERT INTO reminders (user_id,text,due_at,timezone,recurrence,status,created_at,updated_at) VALUES (?,?,?,?,?,'active',?,?) RETURNING *", (user_id,text,due_at.isoformat(),timezone,recurrence_value,now,now))
        _ensure_delivery(conn, row["id"], due_at.isoformat())
        return row


def list_reminders(user_id=None, status="active"):
    user_id = user_id or config.SHEILA_USER_ID
    initialize()
    with connection() as conn: return _all(conn, "SELECT * FROM reminders WHERE user_id = ? AND status = ? ORDER BY due_at, id", (user_id,status))


def update_reminder(reminder_id, *, due_at=None, text=None):
    initialize(); changes = {}; params = []
    if due_at is not None: changes["due_at"] = due_at.isoformat()
    if text is not None: changes["text"] = text
    if not changes: return None
    changes["updated_at"] = datetime.now(config.get_sheila_timezone()).isoformat()
    with connection() as conn:
        conn.execute(_sql("UPDATE reminders SET " + ", ".join(f"{key} = ?" for key in changes) + " WHERE id = ? AND status = 'active'"), (*changes.values(), reminder_id))
        return _one(conn, "SELECT * FROM reminders WHERE id = ?", (reminder_id,))


def set_reminder_status(reminder_id, status):
    if status not in {"active", "cancelled", "completed"}: raise OperationalStoreError("Invalid reminder status.")
    initialize()
    with connection() as conn:
        conn.execute(_sql("UPDATE reminders SET status = ?, updated_at = ? WHERE id = ?"), (status,datetime.now(config.get_sheila_timezone()).isoformat(),reminder_id))
        return _one(conn, "SELECT * FROM reminders WHERE id = ?", (reminder_id,))


def _ensure_delivery(conn, reminder_id, occurrence_at):
    sql = "INSERT INTO reminder_deliveries (reminder_id,occurrence_at,status) VALUES (?,?,'pending') ON CONFLICT(reminder_id,occurrence_at) DO NOTHING"
    conn.execute(_sql(sql), (reminder_id, occurrence_at))


def save_pending(user_id, text, due_date, timezone, expires_at):
    initialize(); now = datetime.now(config.get_sheila_timezone()).isoformat()
    with connection() as conn:
        conn.execute(_sql("INSERT INTO pending_reminder_clarifications (user_id,text,due_date,timezone,created_at,expires_at) VALUES (?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET text=excluded.text,due_date=excluded.due_date,timezone=excluded.timezone,created_at=excluded.created_at,expires_at=excluded.expires_at"), (user_id,text,due_date,timezone,now,expires_at.isoformat()))


def take_pending(user_id, now):
    initialize()
    with connection() as conn:
        row = _one(conn, "SELECT * FROM pending_reminder_clarifications WHERE user_id = ? AND expires_at > ?", (user_id,now.isoformat()))
        if row: conn.execute(_sql("DELETE FROM pending_reminder_clarifications WHERE user_id = ?"), (user_id,))
        return row


def claim_due(now, lease_seconds=120):
    """Atomically claim one due occurrence. A stale claim is retryable."""
    initialize(); lease = (now + timedelta(seconds=lease_seconds)).isoformat()
    with connection() as conn:
        if _sqlite():
            conn.execute("BEGIN IMMEDIATE")
            row = _one(conn, "SELECT d.*, r.text, r.user_id, r.timezone, r.recurrence FROM reminder_deliveries d JOIN reminders r ON r.id=d.reminder_id WHERE r.status='active' AND d.occurrence_at <= ? AND (d.status IN ('pending','failed') OR (d.status='claimed' AND d.claimed_until < ?)) ORDER BY d.occurrence_at LIMIT 1", (now.isoformat(),now.isoformat()))
            if not row: return None
            conn.execute(_sql("UPDATE reminder_deliveries SET status='claimed', attempt_count=attempt_count+1, claimed_until=?, last_error=NULL WHERE id=?"), (lease,row["id"]))
        else:
            row = _one(conn, "SELECT d.*, r.text, r.user_id, r.timezone, r.recurrence FROM reminder_deliveries d JOIN reminders r ON r.id=d.reminder_id WHERE r.status='active' AND d.occurrence_at <= ? AND (d.status IN ('pending','failed') OR (d.status='claimed' AND d.claimed_until < ?)) ORDER BY d.occurrence_at FOR UPDATE SKIP LOCKED LIMIT 1", (now.isoformat(),now.isoformat()))
            if not row: return None
            conn.execute(_sql("UPDATE reminder_deliveries SET status='claimed', attempt_count=attempt_count+1, claimed_until=?, last_error=NULL WHERE id=?"), (lease,row["id"]))
        row["claimed_until"] = lease; row["status"] = "claimed"; return row


def complete_delivery(delivery, now, error=None):
    with connection() as conn:
        if error:
            conn.execute(_sql("UPDATE reminder_deliveries SET status='failed', claimed_until=NULL, last_error=? WHERE id=?"), (str(error)[:1000],delivery["id"])); return False
        conn.execute(_sql("UPDATE reminder_deliveries SET status='sent', sent_at=?, claimed_until=NULL WHERE id=?"), (now.isoformat(),delivery["id"]))
        conn.execute(_sql("UPDATE reminders SET last_sent_at=?, updated_at=? WHERE id=?"), (now.isoformat(),now.isoformat(),delivery["reminder_id"]))
        recurrence = json.loads(delivery["recurrence"]) if delivery.get("recurrence") else None
        if recurrence and recurrence.get("type") == "monthly_day":
            from calendar import monthrange
            due = datetime.fromisoformat(delivery["occurrence_at"]); month = due.month % 12 + 1; year = due.year + (due.month == 12); day = min(int(recurrence["day"]), monthrange(year,month)[1]); nxt = due.replace(year=year,month=month,day=day)
            _ensure_delivery(conn, delivery["reminder_id"], nxt.isoformat())
        return True
