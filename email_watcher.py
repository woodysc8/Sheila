"""
Sheila email watcher.

Primary approach: leverages your existing Gmail filters/labels via Gmail's
native search syntax (X-GM-RAW) -- if your filters already sort important
mail into a label, this catches it directly with ZERO AI calls.

Optional: if ENABLE_AI_FALLBACK is True in config.py, anything not caught by
your filter query also gets a Gemini judgment call as a safety net. Off by
default to keep API usage minimal, per your filters doing the real work.

Run this in its OWN terminal window, separate from main.py -- they're
independent processes. Ctrl+C to stop.
"""

import imaplib
import email
from email.header import decode_header
from email.utils import parseaddr
import re
import time
import sqlite3

from google import genai
from plyer import notification

import config
import tts
import scheduler
import memory

_client = genai.Client(api_key=config.GEMINI_API_KEY) if config.ENABLE_AI_FALLBACK else None

TRIAGE_PROMPT = """You are triaging an email to decide if it's important
enough to interrupt the user with a notification right now. Be conservative --
most email is not urgent. Reply with ONLY "IMPORTANT" or "SKIP", nothing else.

From: {sender}
Subject: {subject}
Preview: {preview}
"""


def _init_seen_db():
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS seen_emails (uid TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()


def _is_seen(uid: str) -> bool:
    conn = sqlite3.connect(config.DB_PATH)
    row = conn.execute("SELECT 1 FROM seen_emails WHERE uid = ?", (uid,)).fetchone()
    conn.close()
    return row is not None


def _mark_seen(uid: str):
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("INSERT OR IGNORE INTO seen_emails (uid) VALUES (?)", (uid,))
    conn.commit()
    conn.close()


def _decode(header_value):
    if not header_value:
        return ""
    parts = decode_header(header_value)
    decoded = ""
    for part, enc in parts:
        decoded += part.decode(enc or "utf-8", errors="ignore") if isinstance(part, bytes) else part
    return decoded


def _get_preview(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    return part.get_payload(decode=True).decode(errors="ignore")
                except Exception:
                    return ""
    else:
        try:
            return msg.get_payload(decode=True).decode(errors="ignore")
        except Exception:
            return ""
    return ""


def _fetch_by_uid(imap, uid_bytes):
    status, msg_data = imap.uid("fetch", uid_bytes, "(RFC822)")
    if status != "OK" or not msg_data or msg_data[0] is None:
        return None
    return email.message_from_bytes(msg_data[0][1])


def _clean_sender_name(raw_from: str) -> str:
    """'Callie Cox' via Ritholtz Wealth Management <ritholtz@x.com> -> Callie Cox"""
    name, addr = parseaddr(raw_from)
    name = name.strip().strip("'\"")
    if " via " in name:
        name = name.split(" via ")[0].strip().strip("'\"")
    if not name:
        name = addr.split("@")[0] if addr else "Someone"
    return name


def _clean_subject(raw_subject: str) -> str:
    """Re: Re: Bloomberg Media Request - "The Close" -> Bloomberg Media Request"""
    subj = raw_subject.strip()
    for _ in range(3):  # strip repeated Re:/Fwd: prefixes
        subj = re.sub(r'^(re|fwd|fw)\s*:\s*', '', subj, flags=re.IGNORECASE).strip()
    subj = subj.strip('"\'')
    if " - " in subj:
        subj = subj.split(" - ")[0].strip()
    return subj


def _humanize(sender_raw: str, subject_raw: str) -> str:
    name = _clean_sender_name(sender_raw)
    topic = _clean_subject(subject_raw)
    if topic:
        return f"{name} emailed you in the {topic} thread."
    return f"{name} emailed you."


def _notify(sender, subject, preview=""):
    human = _humanize(sender, subject)
    memory.log_email(_clean_sender_name(sender), subject, preview)
    memory.queue_notification("email", human)  # always available for "catch me up" etc.

    if scheduler.should_notify_now():
        print(f"[email] IMPORTANT -- silent popup: {human}")
        safe_title = human[:60] + ("…" if len(human) > 60 else "")
        try:
            notification.notify(title="Sheila", message=safe_title, timeout=15)
        except Exception as e:
            print(f"[email] Desktop notification failed (non-fatal): {e}")
        if config.SPEAK_NOTIFICATIONS_ALOUD:
            tts.speak(human)
    else:
        reason = "in a meeting" if scheduler.is_in_meeting() else "quiet hours"
        print(f"[email] Queued ({reason}): {human}")


def check_inbox():
    imap = imaplib.IMAP4_SSL(config.IMAP_SERVER)
    imap.login(config.EMAIL_ADDRESS, config.EMAIL_APP_PASSWORD)
    imap.select("INBOX")

    # --- Path 1: dedicated Morning Brew catch-all, zero AI cost ---
    _run_query_and_notify(imap, config.MORNING_BREW_QUERY)

    # --- Path 2: your filter-driven Gmail query, zero AI cost ---
    _run_query_and_notify(imap, config.GMAIL_NOTIFY_QUERY)

    # --- Path 3: distribution list -- anyone emailing TO or CC'ing these
    # addresses, zero AI cost ---
    if config.DISTRO_HANDLES:
        handles_or = " OR ".join(config.DISTRO_HANDLES)
        distro_query = f"is:unread (to:({handles_or}) OR cc:({handles_or}))"
        _run_query_and_notify(imap, distro_query)

    # --- Path 3: replies to threads you sent mail in (subject starts with
    # "Re:" heuristic) -- zero AI cost, but not perfectly precise ---
    if config.NOTIFY_ON_REPLIES:
        status, uids = imap.uid("search", None, "UNSEEN")
        if status == "OK":
            for uid in uids[0].split():
                uid_str = uid.decode()
                if _is_seen(f"seen:{uid_str}"):
                    continue
                msg = _fetch_by_uid(imap, uid)
                if msg is None:
                    continue
                subject = _decode(msg.get("Subject", ""))
                if subject.strip().lower().startswith("re:"):
                    sender = _decode(msg.get("From", ""))
                    _notify(sender, subject, preview=_get_preview(msg))
                _mark_seen(f"seen:{uid_str}")

    # --- Path 4 (optional): keywords + Gemini for anything everything else
    # above missed ---
    if config.ENABLE_AI_FALLBACK:
        status, uids = imap.uid("search", None, "UNSEEN")
        if status == "OK":
            for uid in uids[0].split():
                uid_str = uid.decode()
                if _is_seen(f"seen:{uid_str}"):
                    continue
                msg = _fetch_by_uid(imap, uid)
                if msg is None:
                    continue
                sender = _decode(msg.get("From", ""))
                subject = _decode(msg.get("Subject", ""))
                preview = _get_preview(msg)

                text_blob = f"{subject} {preview}".lower()
                important = any(kw in text_blob for kw in config.IMPORTANT_KEYWORDS)

                if not important:
                    try:
                        prompt = TRIAGE_PROMPT.format(sender=sender, subject=subject, preview=preview[:300])
                        response = _client.models.generate_content(model=config.GEMINI_MODEL, contents=prompt)
                        important = "IMPORTANT" in response.text.upper()
                    except Exception as e:
                        print(f"[email] Triage error: {e}")

                if important:
                    _notify(sender, subject, preview=preview)
                _mark_seen(f"seen:{uid_str}")

    imap.logout()


def _run_query_and_notify(imap, query: str):
    status, uids = imap.uid("search", None, "X-GM-RAW", f'"{query}"')
    if status != "OK":
        return
    for uid in uids[0].split():
        uid_str = uid.decode()
        if _is_seen(f"seen:{uid_str}"):
            continue
        msg = _fetch_by_uid(imap, uid)
        if msg is None:
            continue
        sender = _decode(msg.get("From", ""))
        subject = _decode(msg.get("Subject", ""))
        _notify(sender, subject, preview=_get_preview(msg))
        _mark_seen(f"seen:{uid_str}")


def main():
    _init_seen_db()
    memory.init_db()  # ensures pending_notifications table exists too
    mode = "filter query only (no AI calls)" if not config.ENABLE_AI_FALLBACK else "filter query + AI fallback"
    print(f"[email] Watching {config.EMAIL_ADDRESS} -- {mode}. Checking every {config.EMAIL_CHECK_INTERVAL_SECONDS}s. Ctrl+C to stop.")
    print(f"[email] Filter query: {config.GMAIL_NOTIFY_QUERY}")
    while True:
        try:
            check_inbox()
            print(f"[email] Checked at {time.strftime('%H:%M:%S')} -- nothing new to report.")
        except Exception as e:
            print(f"[email] Error: {e}")
        time.sleep(config.EMAIL_CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
