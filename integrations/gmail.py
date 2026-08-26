"""Small, read-only Gmail helpers."""

import base64
from email.utils import parseaddr

from .google_auth import GoogleAuthError, build_service


def _header(headers: list[dict], name: str) -> str:
    return next((item.get("value", "") for item in headers if item.get("name", "").lower() == name.lower()), "")


def _decode(data: str) -> str:
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")
    except (ValueError, UnicodeDecodeError):
        return ""


def _body(payload: dict) -> str:
    if payload.get("mimeType", "").startswith("text/plain") and payload.get("body", {}).get("data"):
        return _decode(payload["body"]["data"])
    for part in payload.get("parts", []):
        text = _body(part)
        if text:
            return text
    return ""


def normalize_message(message: dict) -> dict[str, str]:
    payload = message.get("payload", {})
    headers = payload.get("headers", [])
    sender = _header(headers, "From")
    _display, sender_email = parseaddr(sender)
    return {
        "id": message.get("id", ""), "thread_id": message.get("threadId", ""),
        "sender": sender, "sender_email": sender_email, "subject": _header(headers, "Subject") or "(no subject)",
        "date": _header(headers, "Date"), "snippet": message.get("snippet", ""),
        "body": _body(payload).strip()[:4000],
    }


def search_messages(query: str = "in:inbox", limit: int = 10) -> list[dict[str, str]]:
    """Return up to ``limit`` matching messages, newest first as Gmail returns them."""
    try:
        service = build_service("gmail", "v1")
        listing = service.users().messages().list(userId="me", q=query, maxResults=min(max(limit, 1), 20)).execute()
        return [normalize_message(service.users().messages().get(userId="me", id=item["id"], format="full").execute()) for item in listing.get("messages", [])]
    except GoogleAuthError:
        raise
    except Exception as exc:
        raise GoogleAuthError("Gmail search failed.") from exc


def get_message(message_id: str) -> dict[str, str]:
    try:
        return normalize_message(build_service("gmail", "v1").users().messages().get(userId="me", id=message_id, format="full").execute())
    except GoogleAuthError:
        raise
    except Exception as exc:
        raise GoogleAuthError("Gmail message retrieval failed.") from exc
