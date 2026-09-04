import hashlib
import hmac
import http.client
import json
import os
import sqlite3
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
    return {"id": "evt_test_1", "type": "message.inbound", "timestamp": 1, "senderId": "sender_test", "data": {
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
        initialization_completed = False

        def initialize_memory() -> None:
            nonlocal initialization_completed
            initialization_completed = True

        def create_server(*args: object) -> unittest.mock.Mock:
            self.assertTrue(initialization_completed)
            return server

        with patch.object(zavu_webhook.zavu, "webhook_secret", return_value=SECRET), \
             patch.object(zavu_webhook.config, "get_zavu_webhook_port", return_value=3017), \
             patch.object(zavu_webhook.memory, "init_db", side_effect=initialize_memory) as init_db, \
             patch.object(zavu_webhook, "ThreadingHTTPServer", side_effect=create_server) as http_server:
            with self.assertRaisesRegex(RuntimeError, "stop test server"):
                zavu_webhook.run_server()
        init_db.assert_called_once_with()
        http_server.assert_called_once_with(("0.0.0.0", 3017), zavu_webhook.ZavuWebhookHandler)

    def test_missing_api_key_fails_without_leaking_a_value(self):
        with patch.object(zavu.config, "ZAVU_API_KEY", ""):
            with self.assertRaisesRegex(zavu.ZavuError, "Zavu is not configured") as raised:
                zavu.send_text("+14155551234", "whatsapp", "hello", "sender_test")
        self.assertNotIn("Bearer", str(raised.exception))

    def test_outbound_whatsapp_uses_documented_request_format(self):
        response = unittest.mock.Mock(status_code=202)
        response.json.return_value = {"message": {"id": "msg_1", "status": "queued"}}
        response.raise_for_status.return_value = None
        with patch.object(zavu.config, "ZAVU_API_KEY", "zv_test_key"), \
             patch.object(zavu.requests, "post", return_value=response) as post:
            result = zavu.send_text("+14155551234", "whatsapp", "Hello Sheila", "sender_test")
        self.assertEqual(result["message"]["status"], "queued")
        self.assertEqual(post.call_args.args[0], zavu.ZAVU_MESSAGES_URL)
        self.assertEqual(post.call_args.kwargs["json"], {"to": "+14155551234", "channel": "whatsapp", "messageType": "text", "text": "Hello Sheila"})
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer zv_test_key")

    def test_outbound_whatsapp_logs_only_safe_response_diagnostics(self):
        response = unittest.mock.Mock(status_code=202)
        response.json.return_value = {
            "message": {
                "id": "msg_private",
                "providerMessageId": "provider_private",
                "status": "queued",
                "channel": "whatsapp",
                "messageType": "text",
                "to": "+14155551234",
                "from": "+14155550000",
                "text": "private message text",
                "content": {"url": "https://private.example"},
                "metadata": {"secret": "private metadata"},
                "conversationId": "conversation_private",
                "createdAt": "2026-09-01T00:00:00Z",
                "errorCode": "private_error_code",
                "errorMessage": "private error message",
            },
            "requestId": "request_private",
        }
        response.raise_for_status.return_value = None
        with patch.object(zavu.config, "ZAVU_API_KEY", "zv_test_key"), \
             patch.object(zavu.requests, "post", return_value=response), \
             patch("builtins.print") as log:
            zavu.send_text("+14155551234", "whatsapp", "private message text", "sender_test")
            diagnostic = log.call_args.args[0]
        self.assertEqual(
            diagnostic,
            "[zavu] Outbound response http_status=202; json_parsed=yes; channel=whatsapp; "
            "top_level_keys=message,requestId; message_is_object=yes; "
            "message_keys=channel,content,conversationId,createdAt,errorCode,errorMessage,from,id,messageType,metadata,providerMessageId,status,text,to; "
            "status=queued; channel=whatsapp; messageType=text; has_id=yes; "
            "has_provider_message_id=yes; has_error_code=yes; has_error_message=yes; "
            "Zavu accepted request=yes",
        )
        self.assertNotIn("msg_private", diagnostic)
        self.assertNotIn("provider_private", diagnostic)
        self.assertNotIn("request_private", diagnostic)
        self.assertNotIn("+14155551234", diagnostic)
        self.assertNotIn("+14155550000", diagnostic)
        self.assertNotIn("private message text", diagnostic)
        self.assertNotIn("private.example", diagnostic)
        self.assertNotIn("private metadata", diagnostic)
        self.assertNotIn("conversation_private", diagnostic)
        self.assertNotIn("2026-09-01T00:00:00Z", diagnostic)
        self.assertNotIn("private_error_code", diagnostic)
        self.assertNotIn("private error message", diagnostic)

    def test_extracts_only_supported_text_inbound_messages(self):
        self.assertEqual(zavu.extract_inbound_text_event(_event("Good morning")), ("whatsapp", "+14155551234", "Good morning", "sender_test"))
        non_text = _event()
        non_text["data"]["messageType"] = "image"
        self.assertIsNone(zavu.extract_inbound_text_event(non_text))

    def test_extracts_telegram_text_and_sends_on_telegram(self):
        event = {
            "type": "message.inbound",
            "senderId": "telegram_sender_test",
            "data": {
                "from": "telegram:123456789",
                "channel": "telegram",
                "messageType": "text",
                "text": "the message",
            },
        }
        self.assertEqual(zavu.extract_inbound_text_event(event), ("telegram", "123456789", "the message", "telegram_sender_test"))
        response = unittest.mock.Mock(status_code=202)
        response.json.return_value = {"message": {"id": "msg_telegram", "status": "queued"}}
        response.raise_for_status.return_value = None
        with patch.object(zavu.config, "ZAVU_API_KEY", "zv_test_key"), \
             patch.object(zavu.requests, "post", return_value=response) as post:
            zavu.send_text("123456789", "telegram", "Hello Sheila", "telegram_sender_test")
        self.assertEqual(post.call_args.kwargs["json"], {"to": "123456789", "text": "Hello Sheila", "channel": "telegram"})
        self.assertEqual(post.call_args.kwargs["headers"]["Zavu-Sender"], "telegram_sender_test")

    def test_signature_verification_accepts_valid_and_rejects_tampered_body(self):
        body = json.dumps(_event(), separators=(",", ":")).encode()
        timestamp = 1_700_000_000
        header = _signature(body, timestamp)
        self.assertTrue(zavu.verify_webhook_signature(body, header, SECRET, now=timestamp))
        self.assertFalse(zavu.verify_webhook_signature(body + b"x", header, SECRET, now=timestamp))

    def test_inbound_message_uses_existing_sheila_handler_then_zavu(self):
        with patch.object(zavu_webhook, "process_message", return_value="[ASANA RESULTS]\nTask A") as process, \
             patch.object(zavu_webhook.zavu, "send_text") as send, \
             patch.object(zavu_webhook, "_log") as log:
            zavu_webhook.process_zavu_event(_event())
        process.assert_called_once_with("What is due today in Asana?")
        send.assert_called_once_with("+14155551234", "whatsapp", "[ASANA RESULTS]\nTask A", "sender_test")
        self.assertEqual(
            [call.args[0] for call in log.call_args_list],
            [
                "Event passed the inbound WhatsApp text extraction check",
                "Sheila process_message started",
                "Sheila process_message completed successfully",
                "Zavu outbound send started",
                "Inbound sender diagnostic channel=whatsapp; sender_type=str; sender_repr='+14155551234'; sender_id_type=str; sender_is_digits=False",
                "Zavu outbound send completed successfully",
            ],
        )

    def test_telegram_inbound_message_uses_telegram_outbound_channel(self):
        event = _event("Good morning")
        event["data"].update({"channel": "telegram", "from": "telegram:123456789"})
        with patch.object(zavu_webhook, "process_message", return_value="Hello"), \
             patch.object(zavu_webhook.zavu, "send_text") as send:
            zavu_webhook.process_zavu_event(event)
        send.assert_called_once_with("123456789", "telegram", "Hello", "sender_test")

    def test_background_processing_failure_log_is_redacted(self):
        sensitive_error = RuntimeError("message content +14155551234 whsec_secret")
        with patch.object(zavu_webhook, "process_zavu_event", side_effect=sensitive_error), \
             patch.object(zavu_webhook, "_log") as log:
            zavu_webhook._process_safely(_event())
        messages = [call.args[0] for call in log.call_args_list]
        self.assertEqual(messages[0], "Background processing started")
        self.assertEqual(messages[1], "Background processing failed (RuntimeError); no message data logged")
        self.assertNotIn("+14155551234", messages[1])
        self.assertNotIn("whsec_secret", messages[1])

    def test_sqlite_operational_error_identifies_memory_persistence_without_details(self):
        sensitive_error = sqlite3.OperationalError("no such table: exchanges for +14155551234")
        with patch.object(zavu_webhook, "process_zavu_event", side_effect=sensitive_error), \
             patch.object(zavu_webhook, "_log") as log:
            zavu_webhook._process_safely(_event())
        self.assertEqual(
            log.call_args_list[1].args[0],
            "Background processing failed in SQLite memory persistence "
            "(OperationalError); database initialization or writable persistent storage is required",
        )
        self.assertNotIn("+14155551234", log.call_args_list[1].args[0])

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
