import base64
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from integrations import calendar, drive, gmail
import config
from integrations.google_auth import GoogleAuthError, READ_ONLY_SCOPES, load_credentials


class GoogleAuthTests(unittest.TestCase):
    def test_missing_credential_file_fails_cleanly(self):
        with self.assertRaises(GoogleAuthError):
            load_credentials("does-not-exist.json")

    def test_config_uses_the_authorized_user_credential_file(self):
        self.assertTrue(config.GOOGLE_OAUTH_CREDENTIALS_FILE.endswith(".oauth2.sam@streetcredpr.com.json"))

    @patch("google.oauth2.credentials.Credentials.from_authorized_user_file")
    def test_credential_file_loads(self, loader):
        credentials = MagicMock(valid=True)
        loader.return_value = credentials
        with tempfile.NamedTemporaryFile() as credential_file:
            self.assertIs(load_credentials(credential_file.name), credentials)
        loader.assert_called_once_with(credential_file.name, READ_ONLY_SCOPES)

    @patch("google.oauth2.credentials.Credentials.from_authorized_user_file", side_effect=ValueError("bad json"))
    def test_invalid_credentials_fail_cleanly(self, _loader):
        with tempfile.NamedTemporaryFile() as credential_file:
            with self.assertRaises(GoogleAuthError):
                load_credentials(credential_file.name)

    @patch("google.auth.transport.requests.Request")
    @patch("google.oauth2.credentials.Credentials.from_authorized_user_file")
    def test_expired_credentials_refresh_in_memory(self, loader, request):
        credentials = MagicMock(valid=False, expired=True, refresh_token="refresh-token")
        loader.return_value = credentials
        with tempfile.NamedTemporaryFile() as credential_file:
            self.assertIs(load_credentials(credential_file.name), credentials)
        credentials.refresh.assert_called_once()


class GmailTests(unittest.TestCase):
    def test_normalize_message_decodes_plain_text_body(self):
        encoded = base64.urlsafe_b64encode(b"Hello from the message").decode().rstrip("=")
        message = {"id": "m1", "snippet": "Hello", "payload": {"mimeType": "text/plain", "headers": [
            {"name": "From", "value": "Sam <sam@example.com>"}, {"name": "Subject", "value": "Budget"}, {"name": "Date", "value": "Mon"}], "body": {"data": encoded}}}
        result = gmail.normalize_message(message)
        self.assertEqual(result["sender_email"], "sam@example.com")
        self.assertEqual(result["subject"], "Budget")
        self.assertEqual(result["body"], "Hello from the message")

    @patch("integrations.gmail.build_service")
    def test_search_uses_mocked_api_and_normalizes_results(self, build_service):
        service = MagicMock()
        service.users().messages().list().execute.return_value = {"messages": [{"id": "m1"}]}
        service.users().messages().get().execute.return_value = {"id": "m1", "payload": {"headers": []}}
        build_service.return_value = service
        self.assertEqual(gmail.search_messages("budget"), [{"id": "m1", "thread_id": "", "sender": "", "sender_email": "", "subject": "(no subject)", "date": "", "snippet": "", "body": ""}])
        self.assertEqual(
            service.users().messages().list.call_args.kwargs,
            {"userId": "me", "q": "budget", "maxResults": 10},
        )


class CalendarTests(unittest.TestCase):
    def test_event_normalization(self):
        event = calendar.normalize_event({"id": "e1", "summary": "Standup", "start": {"dateTime": "2026-08-24T09:00:00-04:00"}, "end": {"dateTime": "2026-08-24T09:30:00-04:00"}})
        self.assertEqual(event["title"], "Standup")
        self.assertIn("09:00", event["start"])

    @patch("integrations.calendar.build_service")
    def test_date_range_is_sent_to_api(self, build_service):
        service = MagicMock()
        service.events().list().execute.return_value = {"items": []}
        build_service.return_value = service
        start = datetime(2026, 8, 24, tzinfo=timezone.utc)
        calendar.get_events(start, start + timedelta(days=1))
        kwargs = service.events().list.call_args.kwargs
        self.assertEqual(kwargs["timeMin"], start.isoformat())
        self.assertEqual(kwargs["timeMax"], (start + timedelta(days=1)).isoformat())


class DriveTests(unittest.TestCase):
    def test_document_mention_is_not_a_client_relationship(self):
        self.assertFalse(drive.has_explicit_client_relationship("Dispatch is mentioned in this folder overview.", "Dispatch"))

    def test_explicit_client_relationship_is_recognized(self):
        self.assertTrue(drive.has_explicit_client_relationship("Client: Dispatch", "Dispatch"))
        self.assertTrue(drive.has_explicit_client_relationship("Dispatch is a current client", "Dispatch"))

    @patch("integrations.drive.build_service")
    def test_search_normalizes_results(self, build_service):
        service = MagicMock()
        service.files().list().execute.return_value = {"files": [{"id": "f1", "name": "Media list", "mimeType": "application/vnd.google-apps.document"}]}
        build_service.return_value = service
        files = drive.search_files("media list")
        self.assertEqual(files[0]["name"], "Media list")
        self.assertEqual(files[0]["id"], "f1")
        self.assertEqual(service.files().list.call_args.kwargs["pageSize"], 10)


if __name__ == "__main__":
    unittest.main()
