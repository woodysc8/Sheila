"""
Simple memory: every exchange gets logged. Recent turns + anything tagged
'important' get pulled into context for each new request.

Start dumb and reliable — upgrade to a vector DB later once this feels
limiting (e.g. when you want semantic recall of something from 3 weeks ago
that isn't in the recent window).
"""

import sqlite3
import os
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
    conn.commit()
    conn.close()


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
