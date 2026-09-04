"""
Simple memory: every exchange gets logged. Recent turns + anything tagged
'important' get pulled into context for each new request.

Start dumb and reliable — upgrade to a vector DB later once this feels
limiting (e.g. when you want semantic recall of something from 3 weeks ago
that isn't in the recent window).
"""

import sqlite3
import os
import json
from datetime import datetime
import config


def init_db():
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS exchanges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            user_text TEXT NOT NULL,
            assistant_text TEXT NOT NULL,
            important INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pending_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            source TEXT NOT NULL,
            summary TEXT NOT NULL,
            delivered INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS structured_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            content TEXT NOT NULL,
            source TEXT NOT NULL,
            source_id TEXT,
            importance INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            metadata TEXT NOT NULL DEFAULT '{}'
        )
    """)
    conn.commit()
    conn.close()


def _connect():
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _memory_row(row):
    item = dict(row)
    try:
        item["metadata"] = json.loads(item["metadata"] or "{}")
    except (TypeError, ValueError):
        item["metadata"] = {}
    return item


def remember(category: str, content: str, source: str, source_id: str = None,
             importance: int = 0, metadata: dict = None) -> dict:
    """Persist one structured fact while keeping conversation exchanges separate."""
    init_db()
    now = datetime.now().isoformat()
    conn = _connect()
    cursor = conn.execute(
        """INSERT INTO structured_memories
           (category, content, source, source_id, importance, created_at, updated_at, metadata)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (category, content, source, source_id, int(importance), now, now,
         json.dumps(metadata or {})),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM structured_memories WHERE id = ?", (cursor.lastrowid,)).fetchone()
    conn.close()
    return _memory_row(row)


def recall(query: str = "", category: str = None, limit: int = 10) -> list[dict]:
    """Return bounded structured memories using simple reliable text matching."""
    init_db()
    conn = _connect()
    clauses, params = [], []
    if category:
        clauses.append("category = ?")
        params.append(category)
    words = [word.lower() for word in query.split() if len(word) >= 3]
    if words:
        clauses.append("(" + " OR ".join("LOWER(content) LIKE ?" for _ in words) + ")")
        params.extend(f"%{word}%" for word in words)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        "SELECT * FROM structured_memories" + where +
        " ORDER BY importance DESC, updated_at DESC LIMIT ?", (*params, limit)
    ).fetchall()
    conn.close()
    return [_memory_row(row) for row in rows]


def update(memory_id: int, **changes) -> dict:
    """Update a structured memory and preserve its original creation time."""
    allowed = {"category", "content", "source", "source_id", "importance", "metadata"}
    values = {key: value for key, value in changes.items() if key in allowed}
    if not values:
        raise ValueError("At least one memory field is required")
    if "metadata" in values:
        values["metadata"] = json.dumps(values["metadata"] or {})
    values["updated_at"] = datetime.now().isoformat()
    init_db()
    conn = _connect()
    assignments = ", ".join(f"{key} = ?" for key in values)
    cursor = conn.execute(
        f"UPDATE structured_memories SET {assignments} WHERE id = ?",
        (*values.values(), memory_id),
    )
    if cursor.rowcount == 0:
        conn.close()
        raise KeyError(memory_id)
    conn.commit()
    row = conn.execute("SELECT * FROM structured_memories WHERE id = ?", (memory_id,)).fetchone()
    conn.close()
    return _memory_row(row)


def forget(memory_id: int) -> bool:
    """Delete one structured memory by id."""
    init_db()
    conn = _connect()
    cursor = conn.execute("DELETE FROM structured_memories WHERE id = ?", (memory_id,))
    conn.commit()
    conn.close()
    return cursor.rowcount > 0


def get_structured_context(query: str = "", limit: int = 8) -> str:
    items = recall(query=query, limit=limit)
    if not items:
        return "No structured memories yet."
    lines = ["Structured memories:"]
    for item in items:
        provenance = item["source"] + (f"/{item['source_id']}" if item["source_id"] else "")
        lines.append(f"- [{item['category']}; from {provenance}] {item['content']}")
    return "\n".join(lines)


def queue_notification(source: str, summary: str):
    """Called instead of firing a real notification when it's quiet hours
    or you're in a meeting -- saved for the next 'catch me up'."""
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute(
        "INSERT INTO pending_notifications (timestamp, source, summary) VALUES (?, ?, ?)",
        (datetime.now().isoformat(), source, summary),
    )
    conn.commit()
    conn.close()

    # Also keep a durable memory record so later questions can refer to it.
    log_exchange(
        user_text=f"[notification] {source}",
        assistant_text=summary,
        important=True,
    )


def get_pending_notifications():
    conn = sqlite3.connect(config.DB_PATH)
    rows = conn.execute(
        "SELECT timestamp, source, summary FROM pending_notifications "
        "WHERE delivered = 0 ORDER BY id ASC"
    ).fetchall()
    conn.close()
    return rows


def mark_notifications_delivered():
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("UPDATE pending_notifications SET delivered = 1 WHERE delivered = 0")
    conn.commit()
    conn.close()


def set_meeting_status(in_meeting: bool):
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        "INSERT OR REPLACE INTO state (key, value) VALUES ('in_meeting', ?)",
        ("1" if in_meeting else "0",),
    )
    conn.commit()
    conn.close()


def get_meeting_status() -> bool:
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)")
    row = conn.execute("SELECT value FROM state WHERE key = 'in_meeting'").fetchone()
    conn.close()
    return row is not None and row[0] == "1"


def get_state(key: str, default=None):
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)")
    row = conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row[0] if row else default


def set_state(key: str, value: str):
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


def log_slack_message(sender_name: str, channel_name: str, text: str):
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS slack_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            sender_name TEXT NOT NULL,
            channel_name TEXT NOT NULL,
            text TEXT NOT NULL
        )
    """)
    conn.execute(
        "INSERT INTO slack_log (timestamp, sender_name, channel_name, text) VALUES (?, ?, ?, ?)",
        (datetime.now().isoformat(), sender_name, channel_name, text[:2000]),
    )
    conn.commit()
    conn.close()


def get_latest_message_from(name_query: str):
    """Checks BOTH email and Slack logs, returns whichever is more recent.
    Row format: (timestamp, sender_name, context_label, body, platform)
    where context_label is the email subject or the Slack channel name."""
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS email_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
            sender_name TEXT NOT NULL, subject TEXT NOT NULL, body_preview TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS slack_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
            sender_name TEXT NOT NULL, channel_name TEXT NOT NULL, text TEXT NOT NULL
        )
    """)
    email_row = conn.execute(
        "SELECT timestamp, sender_name, subject, body_preview FROM email_log "
        "WHERE LOWER(sender_name) LIKE ? ORDER BY id DESC LIMIT 1",
        (f"%{name_query.lower()}%",),
    ).fetchone()
    slack_row = conn.execute(
        "SELECT timestamp, sender_name, channel_name, text FROM slack_log "
        "WHERE LOWER(sender_name) LIKE ? ORDER BY id DESC LIMIT 1",
        (f"%{name_query.lower()}%",),
    ).fetchone()
    conn.close()

    candidates = []
    if email_row:
        candidates.append((*email_row, "email"))
    if slack_row:
        candidates.append((*slack_row, "slack"))
    if not candidates:
        return None
    candidates.sort(key=lambda r: r[0], reverse=True)
    return candidates[0]


def log_email(sender_name: str, subject: str, body_preview: str):
    """Stores the actual email content so 'what did X say' can look it up
    later -- separate from the short spoken summary in pending_notifications."""
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS email_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            sender_name TEXT NOT NULL,
            subject TEXT NOT NULL,
            body_preview TEXT NOT NULL
        )
    """)
    conn.execute(
        "INSERT INTO email_log (timestamp, sender_name, subject, body_preview) VALUES (?, ?, ?, ?)",
        (datetime.now().isoformat(), sender_name, subject, body_preview[:2000]),
    )
    conn.commit()
    conn.close()


def get_latest_email_from(name_query: str):
    """Fuzzy match on sender name, most recent first. Returns a row or None."""
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS email_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            sender_name TEXT NOT NULL,
            subject TEXT NOT NULL,
            body_preview TEXT NOT NULL
        )
    """)
    row = conn.execute(
        "SELECT timestamp, sender_name, subject, body_preview FROM email_log "
        "WHERE LOWER(sender_name) LIKE ? ORDER BY id DESC LIMIT 1",
        (f"%{name_query.lower()}%",),
    ).fetchone()
    conn.close()
    return row


def log_exchange(user_text: str, assistant_text: str, important: bool = False):
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute(
        "INSERT INTO exchanges (timestamp, user_text, assistant_text, important) VALUES (?, ?, ?, ?)",
        (datetime.now().isoformat(), user_text, assistant_text, int(important)),
    )
    conn.commit()
    conn.close()


def get_context(n: int = None, current_query: str = "") -> str:
    """Pull recent exchanges + all important-tagged ones + anything from the
    full history that shares keywords with the current query, formatted for
    injection into the Gemini prompt as context."""
    n = n or config.MEMORY_CONTEXT_TURNS
    conn = sqlite3.connect(config.DB_PATH)

    recent = conn.execute(
        "SELECT id, timestamp, user_text, assistant_text FROM exchanges "
        "ORDER BY id DESC LIMIT ?", (n,)
    ).fetchall()
    recent_ids = {row[0] for row in recent}

    important = conn.execute(
        "SELECT id, timestamp, user_text, assistant_text FROM exchanges "
        "WHERE important = 1 ORDER BY id DESC LIMIT 20"
    ).fetchall()
    important_ids = {row[0] for row in important}

    relevant = []
    if current_query.strip():
        # crude but effective: match on words 4+ letters long, skip ones
        # already surfaced via recent/important
        keywords = [w for w in current_query.lower().split() if len(w) >= 4]
        if keywords:
            like_clauses = " OR ".join(["LOWER(user_text) LIKE ? OR LOWER(assistant_text) LIKE ?"] * len(keywords))
            params = []
            for kw in keywords:
                params.extend([f"%{kw}%", f"%{kw}%"])
            rows = conn.execute(
                f"SELECT id, timestamp, user_text, assistant_text FROM exchanges "
                f"WHERE {like_clauses} ORDER BY id DESC LIMIT 10", params
            ).fetchall()
            relevant = [r for r in rows if r[0] not in recent_ids and r[0] not in important_ids][:5]

    conn.close()

    lines = []
    if important:
        lines.append("Important things to remember:")
        for _id, ts, u, a in reversed(important):
            lines.append(f"- [{ts[:10]}] User: {u} | Iris: {a}")

    if relevant:
        lines.append("\nPossibly relevant past exchanges:")
        for _id, ts, u, a in reversed(relevant):
            lines.append(f"- [{ts[:10]}] User: {u} | Iris: {a}")

    if recent:
        lines.append("\nRecent conversation:")
        for _id, ts, u, a in reversed(recent):
            lines.append(f"- User: {u}\n  Iris: {a}")

    return "\n".join(lines) if lines else "No prior context yet."


def mark_last_important():
    """Call this when the user says something like 'remember this' right
    after an exchange, to tag the most recent entry as important."""
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute(
        "UPDATE exchanges SET important = 1 WHERE id = (SELECT MAX(id) FROM exchanges)"
    )
    conn.commit()
    conn.close()


def forget_last():
    """Call this when the user says something like 'forget that' right
    after an exchange, to delete the most recent entry entirely."""
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("DELETE FROM exchanges WHERE id = (SELECT MAX(id) FROM exchanges)")
    conn.commit()
    conn.close()
