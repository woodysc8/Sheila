"""Local, signature-verified Zavu webhook server for Sheila's WhatsApp interface.

Run separately from the terminal client: ``python zavu_webhook.py``.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

from integrations import zavu
import config
from sheila_handler import process_message


HOST = "0.0.0.0"
PORT = 3002
WEBHOOK_PATH = "/webhooks/zavu"
_processed_events: set[str] = set()
_processed_lock = threading.Lock()


def process_zavu_event(event: dict) -> None:
    """Send only verified inbound WhatsApp text through Sheila's existing handler."""
    inbound = zavu.extract_inbound_text_event(event)
    if not inbound:
        return
    sender, text = inbound
    response = process_message(text)
    zavu.send_whatsapp_text(sender, response)


class ZavuWebhookHandler(BaseHTTPRequestHandler):
    server_version = "SheilaZavuWebhook/1.0"

    def log_message(self, _format: str, *_args: object) -> None:
        """Avoid logging payloads or credentials in the local HTTP server."""

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != WEBHOOK_PATH:
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
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OK")
                return
            _processed_events.add(event_id)
        # Zavu requires a 2xx acknowledgement within 30 seconds. Process after
        # acknowledgement to avoid retrying a valid event while OpenAI is busy.
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
        threading.Thread(target=_process_safely, args=(event,), daemon=True).start()


def _process_safely(event: dict) -> None:
    try:
        process_zavu_event(event)
    except zavu.ZavuError as exc:
        print(f"[zavu] WhatsApp response could not be delivered: {exc}")
    except Exception as exc:
        print(f"[zavu] Inbound message handling failed: {type(exc).__name__}")


def run_server(host: str = HOST, port: int | None = None) -> None:
    zavu.webhook_secret()  # Refuse to run an unauthenticated public endpoint.
    port = config.get_zavu_webhook_port() if port is None else port
    server = ThreadingHTTPServer((host, port), ZavuWebhookHandler)
    print(f"Sheila Zavu webhook listening on http://{host}:{port}{WEBHOOK_PATH}")
    server.serve_forever()


if __name__ == "__main__":
    try:
        run_server()
    except KeyboardInterrupt:
        pass
