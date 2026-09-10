"""Local, signature-verified Zavu webhook server for Sheila's messaging interface.

Run separately from the terminal client: ``python zavu_webhook.py``.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sqlite3
import threading
from urllib.parse import parse_qs, urlparse

import calendar_page
import calendar_store
from integrations import zavu
import config
import memory
from sheila_handler import process_message


HOST = "0.0.0.0"
PORT = 3002
WEBHOOK_PATH = "/webhooks/zavu"
CALENDAR_PATH = "/calendar"
CALENDAR_API_PATH = "/api/calendar/events"
_processed_events: set[str] = set()
_processed_lock = threading.Lock()


def _log(message: str) -> None:
    """Emit redacted lifecycle diagnostics suitable for hosted-service logs."""
    print(f"[zavu] {message}", flush=True)


def process_zavu_event(event: dict) -> None:
    """Send only verified inbound WhatsApp or Telegram text through Sheila's existing handler."""
    inbound = zavu.extract_inbound_text_event(event)
    if not inbound:
        _log("Event did not pass the inbound WhatsApp text extraction check")
        return

    channel, sender, text, sender_id = inbound
    _log("Event passed the inbound text extraction check")

    _log("Sheila process_message started")
    response = process_message(text)
    _log("Sheila process_message completed successfully")

    _log("Zavu outbound send started")
    _log(
        f"Inbound sender diagnostic channel={channel}; sender_type={type(sender).__name__}; "
        f"sender_repr={sender!r}; sender_id={sender_id!r}; sender_id_type={type(sender_id).__name__}; "
        f"sender_is_digits={sender.isdigit()}"
    )
    zavu.send_text(sender, channel, response, sender_id)
    _log("Zavu outbound send completed successfully")


class ZavuWebhookHandler(BaseHTTPRequestHandler):
    server_version = "SheilaZavuWebhook/1.0"

    def log_message(self, _format: str, *_args: object) -> None:
        """Avoid logging payloads or credentials in the local HTTP server."""

    def _json(self, status: int, payload: object) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 100_000:
                raise ValueError("Request body is too large.")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object.")
            return payload
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise calendar_store.CalendarError("Request body must be a JSON object.") from exc

    def _calendar_api(self, method: str, path: str) -> None:
        try:
            if method == "GET":
                if path != CALENDAR_API_PATH:
                    event_id = int(path.rsplit("/", 1)[1])
                    event = calendar_store.get_event(event_id)
                    self._json(200, event) if event else self._json(404, {"error": "Event not found."})
                    return
                query = parse_qs(urlparse(self.path).query)
                timezone = query.get("timezone", [config.SHEILA_TIMEZONE])[0]
                events = calendar_store.list_events(query.get("start", [""])[0], query.get("end", [""])[0], timezone)
                self._json(200, {"events": events})
                return
            event_id = int(path.rsplit("/", 1)[1]) if path != CALENDAR_API_PATH else None
            if method == "POST":
                payload = self._body_json()
                event = calendar_store.create_event(
                    payload.get("title", ""), payload.get("start", ""), payload.get("end", ""),
                    description=payload.get("description", ""), timezone=payload.get("timezone"), location=payload.get("location", ""),
                )
                self._json(201, event)
                return
            if event_id is None:
                raise calendar_store.CalendarError("An event id is required.")
            if method == "PATCH":
                event = calendar_store.update_event(event_id, **self._body_json())
                if event is None:
                    self._json(404, {"error": "Event not found."})
                else:
                    self._json(200, event)
                return
            if method == "DELETE":
                if not calendar_store.delete_event(event_id):
                    self._json(404, {"error": "Event not found."})
                else:
                    self.send_response(204)
                    self.end_headers()
                return
            self._json(405, {"error": "Method not allowed."})
        except calendar_store.CalendarError as exc:
            self._json(400, {"error": str(exc)})
        except (TypeError, ValueError) as exc:
            self._json(400, {"error": str(exc)})

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        if parsed.path == CALENDAR_PATH:
            body = calendar_page.CALENDAR_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == CALENDAR_API_PATH or parsed.path.startswith(CALENDAR_API_PATH + "/"):
            self._calendar_api("GET", parsed.path)
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if urlparse(self.path).path == CALENDAR_API_PATH:
            self._calendar_api("POST", CALENDAR_API_PATH)
            return
        if urlparse(self.path).path != WEBHOOK_PATH:
            self.send_error(404)
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400, "Invalid content length")
            return
        raw_body = self.rfile.read(content_length)
        try:
            secret = zavu.webhook_secret()
        except zavu.ZavuError:
            self.send_error(503, "Webhook verification is not configured")
            return
        if not zavu.verify_webhook_signature(raw_body, self.headers.get("X-Zavu-Signature", ""), secret):
            self.send_error(401, "Invalid signature")
            return
        _log("Verified inbound webhook received")
        try:
            event = json.loads(raw_body)
        except (TypeError, ValueError):
            self.send_error(400, "Invalid JSON")
            return
        event_id = event.get("id") if isinstance(event, dict) else None
        if not isinstance(event, dict) or not isinstance(event_id, str) or not event_id:
            self.send_error(400, "Invalid event")
            return
        with _processed_lock:
            if event_id in _processed_events:
                _log("Event was already accepted; acknowledging duplicate")
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OK")
                return
            _processed_events.add(event_id)
        _log("Event accepted for background processing")
        # Zavu requires a 2xx acknowledgement within 30 seconds. Process after
        # acknowledgement to avoid retrying a valid event while OpenAI is busy.
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
        threading.Thread(target=_process_safely, args=(event,), daemon=True).start()

    def do_PATCH(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        if parsed.path.startswith(CALENDAR_API_PATH + "/"):
            self._calendar_api("PATCH", parsed.path)
            return
        self.send_error(404)

    def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        if parsed.path.startswith(CALENDAR_API_PATH + "/"):
            self._calendar_api("DELETE", parsed.path)
            return
        self.send_error(404)


def _process_safely(event: dict) -> None:
    try:
        _log("Background processing started")
        process_zavu_event(event)
    except sqlite3.OperationalError:
        _log(
            "Background processing failed in SQLite memory persistence "
            "(OperationalError); database initialization or writable persistent storage is required"
        )
    except zavu.ZavuError as exc:
        _log(f"Background processing failed during Zavu delivery ({type(exc).__name__}); no message data logged")
    except Exception as exc:
        _log(f"Background processing failed ({type(exc).__name__}); no message data logged")


def run_server(host: str = HOST, port: int | None = None) -> None:
    zavu.webhook_secret()  # Refuse to run an unauthenticated public endpoint.
    port = config.get_zavu_webhook_port() if port is None else port
    memory.init_db()
    server = ThreadingHTTPServer((host, port), ZavuWebhookHandler)
    print(f"Sheila Zavu webhook listening on http://{host}:{port}{WEBHOOK_PATH}")
    server.serve_forever()


if __name__ == "__main__":
    try:
        run_server()
    except KeyboardInterrupt:
        pass
