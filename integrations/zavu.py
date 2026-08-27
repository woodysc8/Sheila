"""Small, isolated client for Zavu's WhatsApp messaging and webhook security."""

import hashlib
import hmac
import time

import requests

import config


ZAVU_MESSAGES_URL = "https://api.zavu.dev/v1/messages"
MAX_WEBHOOK_AGE_SECONDS = 300


class ZavuError(RuntimeError):
    """Raised when Zavu cannot send or validate a message."""


def _api_key() -> str:
    if not config.ZAVU_API_KEY or "YOUR_" in config.ZAVU_API_KEY.upper():
        raise ZavuError("Zavu is not configured. Add ZAVU_API_KEY to the environment.")
    return config.ZAVU_API_KEY


def _log_outbound_response(status_code: int, payload: object, json_parsed: bool) -> None:
    """Log only non-sensitive metadata about Zavu's outbound API response."""
    top_level_keys = sorted(payload) if isinstance(payload, dict) else []
    accepted = 200 <= status_code < 300
    print(
        "[zavu] Outbound response "
        f"http_status={status_code}; json_parsed={'yes' if json_parsed else 'no'}; "
        f"top_level_keys={','.join(top_level_keys) or 'none'}; "
        f"Zavu accepted request={'yes' if accepted else 'no'}",
        flush=True,
    )


def send_whatsapp_text(recipient: str, text: str) -> dict:
    """Queue one WhatsApp text response through Zavu's documented API."""
    if not recipient or not text.strip():
        raise ZavuError("Zavu recipient and message text are required.")
    try:
        response = requests.post(
            ZAVU_MESSAGES_URL,
            headers={"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"},
            json={"to": recipient, "channel": "whatsapp", "messageType": "text", "text": text},
            timeout=20,
        )
        try:
            payload = response.json()
        except (TypeError, ValueError):
            payload = None
            _log_outbound_response(response.status_code, payload, json_parsed=False)
            raise ZavuError("Zavu returned an invalid message response.")
        _log_outbound_response(response.status_code, payload, json_parsed=True)
        if response.status_code in {401, 403}:
            raise ZavuError("Zavu authentication failed. Check the local API key.")
        response.raise_for_status()
        return payload
    except ZavuError:
        raise
    except requests.RequestException as exc:
        raise ZavuError("Zavu WhatsApp delivery is unavailable right now.") from exc
    except (TypeError, ValueError) as exc:
        raise ZavuError("Zavu returned an invalid message response.") from exc


def verify_webhook_signature(raw_body: bytes, header: str, secret: str, now: int | None = None) -> bool:
    """Verify documented Zavu v2 signatures, while accepting legacy v1 safely."""
    if not header or not secret:
        return False
    parts: dict[str, str] = {}
    for piece in header.split(","):
        key, separator, value = piece.strip().partition("=")
        if separator:
            parts[key] = value
    try:
        timestamp = int(parts["t"])
    except (KeyError, ValueError):
        return False
    age = (int(time.time()) if now is None else now) - timestamp
    if age > MAX_WEBHOOK_AGE_SECONDS or age < -60:
        return False
    received = parts.get("v2") or parts.get("v1")
    if not received:
        return False
    signed = f"{timestamp}.".encode("utf-8") + raw_body if parts.get("v2") else raw_body
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)


def webhook_secret() -> str:
    if not config.ZAVU_WEBHOOK_SECRET or "YOUR_" in config.ZAVU_WEBHOOK_SECRET.upper():
        raise ZavuError("Zavu webhook verification is not configured. Add ZAVU_WEBHOOK_SECRET to the environment.")
    return config.ZAVU_WEBHOOK_SECRET


def extract_inbound_text_event(event: dict) -> tuple[str, str] | None:
    """Return the opaque sender identifier and text only for WhatsApp text events."""
    data = event.get("data") if event.get("type") == "message.inbound" else None
    if not isinstance(data, dict) or data.get("channel") != "whatsapp" or data.get("messageType") != "text":
        return None
    sender, text = data.get("from"), data.get("text")
    if not isinstance(sender, str) or not isinstance(text, str) or not sender or not text.strip():
        return None
    return sender, text.strip()
