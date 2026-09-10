import http.client
import json
import threading
import unittest
from unittest.mock import patch

import config
import zavu_webhook


class ReminderWorkerEndpointTests(unittest.TestCase):
    def setUp(self):
        self.server = zavu_webhook.ThreadingHTTPServer(("127.0.0.1", 0), zavu_webhook.ZavuWebhookHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.token = patch.object(config, "SHEILA_REMINDER_WORKER_TOKEN", "test-worker-token")
        self.token.start()

    def tearDown(self):
        self.token.stop()
        self.server.shutdown(); self.thread.join(timeout=1); self.server.server_close()

    def post(self, token=None):
        headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1])
        conn.request("POST", zavu_webhook.REMINDER_WORKER_PATH, headers=headers)
        response = conn.getresponse(); body = json.loads(response.read() or b"{}")
        conn.close()
        return response.status, body

    def test_missing_and_invalid_token_are_unauthorized(self):
        self.assertEqual(self.post()[0], 401)
        self.assertEqual(self.post("wrong")[0], 401)

    def test_valid_token_invokes_existing_worker_and_returns_its_counts(self):
        with patch.object(zavu_webhook.reminder_worker, "dispatch_due", return_value={"processed": 2, "sent": 1, "failed": 1}) as dispatch:
            status, body = self.post("test-worker-token")
        self.assertEqual(status, 200); self.assertEqual(body, {"processed": 2, "sent": 1, "failed": 1})
        dispatch.assert_called_once_with()

    def test_repeated_authorized_calls_delegate_to_atomic_worker_claiming(self):
        with patch.object(zavu_webhook.reminder_worker, "dispatch_due", side_effect=[{"processed": 1, "sent": 1, "failed": 0}, {"processed": 0, "sent": 0, "failed": 0}]) as dispatch:
            self.assertEqual(self.post("test-worker-token")[1]["sent"], 1)
            self.assertEqual(self.post("test-worker-token")[1]["sent"], 0)
        self.assertEqual(dispatch.call_count, 2)


if __name__ == "__main__":
    unittest.main()
