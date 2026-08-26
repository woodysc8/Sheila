import hashlib
import hmac
import http.client
import json
import os
import threading
import time
import unittest
from unittest.mock import patch

from integrations import zavu
import zavu_webhook
import sheila_handler


SECRET = "whsec_test_secret"


def _signature(body: bytes, timestamp: int) -> str:
    digest = hmac.new(SECRET.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v2={digest}"


def _event(text: str = "What is due today in Asana?") -> dict:
    return {"id": "evt_test_1", "type": "message.inbound", "timestamp": 1, "data": {
        "messageId": "msg_test", "from": "+14155551234", "to": "+14155550000",
        "channel": "whatsapp", "messageType": "text", "text": text,
    }}


class ZavuIntegrationTests(unittest.TestCase):
    def test_webhook_port_defaults_to_3002(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(zavu_webhook.config.get_zavu_webhook_port(), 3002)

    def test_webhook_port_honors_local_zavu_override(self):
        with patch.dict(os.environ, {"ZAVU_WEBHOOK_PORT": "3017"}, clear=True):
            self.assertEqual(zavu_webhook.config.get_zavu_webhook_port(), 3017)

    def test_webhook_port_honors_render_port_override(self):
        with patch.dict(os.environ, {"PORT": "10000"}, clear=True):
            self.assertEqual(zavu_webhook.config.get_zavu_webhook_port(), 10000)

    def test_render_port_takes_precedence_over_local_zavu_override(self):
        with patch.dict(os.environ, {"PORT": "10000", "ZAVU_WEBHOOK_PORT": "3017"}, clear=True):
            self.assertEqual(zavu_webhook.config.get_zavu_webhook_port(), 10000)

    def test_invalid_webhook_port_falls_back_to_3002(self):
        with patch.dict(os.environ, {"PORT": "not-a-port", "ZAVU_WEBHOOK_PORT": "3017"}, clear=True):
            self.assertEqual(zavu_webhook.config.get_zavu_webhook_port(), 3002)

    def test_run_server_binds_the_configured_port(self):
        server = unittest.mock.Mock()
        server.serve_forever.side_effect = RuntimeError("stop test server")
        with patch.object(zavu_webhook.zavu, "webhook_secret", return_value=SECRET), \
             patch.object(zavu_webhook.config, "get_zavu_webhook_port", return_value=3017), \
             patch.object(zavu_webhook, "ThreadingHTTPServer", return_value=server) as http_server:
            with self.assertRaisesRegex(RuntimeError, "stop test server"):
                zavu_webhook.run_server()
        http_server.assert_called_once_with(("0.0.0.0", 3017), zavu_webhook.ZavuWebhookHandler)

    def test_missing_api_key_fails_without_leaking_a_value(self):
        with patch.object(zavu.config, "ZAVU_API_KEY", ""):
            with self.assertRaisesRegex(zavu.ZavuError, "Zavu is not configured") as raised:
                zavu.send_whatsapp_text("+14155551234", "hello")
        self.assertNotIn("Bearer", str(raised.exception))

    def test_outbound_whatsapp_uses_documented_request_format(self):
        response = unittest.mock.Mock(status_code=202)
        response.json.return_value = {"message": {"id": "msg_1", "status": "queued"}}
        response.raise_for_status.return_value = None
        with patch.object(zavu.config, "ZAVU_API_KEY", "zv_test_key"), \
             patch.object(zavu.requests, "post", return_value=response) as post:
            result = zavu.send_whatsapp_text("+14155551234", "Hello Sheila")
        self.assertEqual(result["message"]["status"], "queued")
        self.assertEqual(post.call_args.args[0], zavu.ZAVU_MESSAGES_URL)
        self.assertEqual(post.call_args.kwargs["json"], {"to": "+14155551234", "channel": "whatsapp", "messageType": "text", "text": "Hello Sheila"})
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer zv_test_key")

    def test_extracts_only_whatsapp_text_inbound_messages(self):
        self.assertEqual(zavu.extract_inbound_text_event(_event("Good morning")), ("+14155551234", "Good morning"))
        non_text = _event()
        non_text["data"]["messageType"] = "image"
        self.assertIsNone(zavu.extract_inbound_text_event(non_text))

    def test_signature_verification_accepts_valid_and_rejects_tampered_body(self):
        body = json.dumps(_event(), separators=(",", ":")).encode()
        timestamp = 1_700_000_000
        header = _signature(body, timestamp)
        self.assertTrue(zavu.verify_webhook_signature(body, header, SECRET, now=timestamp))
        self.assertFalse(zavu.verify_webhook_signature(body + b"x", header, SECRET, now=timestamp))

    def test_inbound_message_uses_existing_sheila_handler_then_zavu(self):
        with patch.object(zavu_webhook, "process_message", return_value="[ASANA RESULTS]\nTask A") as process, \
             patch.object(zavu_webhook.zavu, "send_whatsapp_text") as send:
            zavu_webhook.process_zavu_event(_event())
        process.assert_called_once_with("What is due today in Asana?")
        send.assert_called_once_with("+14155551234", "[ASANA RESULTS]\nTask A")

    def test_whatsapp_adapter_uses_the_same_shared_handler_as_terminal(self):
        self.assertIs(zavu_webhook.process_message, sheila_handler.process_message)

    def test_signed_local_webhook_acknowledges_and_dispatches(self):
        zavu_webhook._processed_events.clear()
        server = zavu_webhook.ThreadingHTTPServer(("127.0.0.1", 0), zavu_webhook.ZavuWebhookHandler)
        thread = threading.Thread(target=server.handle_request, daemon=True)
        body = json.dumps(_event(), separators=(",", ":")).encode()
        timestamp = int(time.time())
        with patch.object(zavu_webhook.zavu, "webhook_secret", return_value=SECRET), \
             patch.object(zavu_webhook, "process_zavu_event") as process:
            thread.start()
            connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
            connection.request("POST", "/webhooks/zavu", body=body, headers={"Content-Type": "application/json", "X-Zavu-Signature": _signature(body, timestamp)})
            response = connection.getresponse()
            response.read()
            thread.join(timeout=1)
            for _ in range(20):
                if process.called:
                    break
                time.sleep(0.01)
        server.server_close()
        self.assertEqual(response.status, 200)
        process.assert_called_once_with(_event())

    def test_duplicate_signed_event_is_acknowledged_without_second_dispatch(self):
        zavu_webhook._processed_events.clear()
        server = zavu_webhook.ThreadingHTTPServer(("127.0.0.1", 0), zavu_webhook.ZavuWebhookHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        body = json.dumps(_event(), separators=(",", ":")).encode()
        timestamp = int(time.time())
        headers = {"Content-Type": "application/json", "X-Zavu-Signature": _signature(body, timestamp)}
        with patch.object(zavu_webhook.zavu, "webhook_secret", return_value=SECRET), \
             patch.object(zavu_webhook, "process_zavu_event") as process:
            thread.start()
            for _ in range(2):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
                connection.request("POST", "/webhooks/zavu", body=body, headers=headers)
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 200)
                connection.close()
            for _ in range(20):
                if process.called:
                    break
                time.sleep(0.01)
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()
        process.assert_called_once_with(_event())


if __name__ == "__main__":
    unittest.main()
