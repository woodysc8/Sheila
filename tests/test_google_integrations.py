import base64
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from integrations import calendar, drive, gmail
import config
from integrations.google_auth import (
    GoogleAuthError, PERSONAL_CALENDAR_SCOPES, READ_ONLY_SCOPES,
    build_personal_calendar_service, build_service, load_credentials,
    load_personal_calendar_credentials,
)


class GoogleAuthTests(unittest.TestCase):
    def test_missing_credential_file_fails_cleanly(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(GoogleAuthError):
                load_credentials("does-not-exist.json")

    def test_config_uses_the_authorized_user_credential_file(self):
        self.assertTrue(config.GOOGLE_OAUTH_CREDENTIALS_FILE.endswith(".oauth2.sam@streetcredpr.com.json"))

    def test_personal_config_uses_distinct_oauth_files(self):
        self.assertTrue(config.SHEILA_PERSONAL_GOOGLE_OAUTH_CLIENT_FILE.endswith("credentials_personal.json"))
        self.assertTrue(config.SHEILA_PERSONAL_GOOGLE_OAUTH_CREDENTIALS_FILE.endswith(".oauth2.personal-calendar.json"))
        self.assertNotEqual(config.SHEILA_PERSONAL_GOOGLE_OAUTH_CREDENTIALS_FILE, config.GOOGLE_OAUTH_CREDENTIALS_FILE)

    @patch("google.oauth2.credentials.Credentials.from_authorized_user_file")
    def test_credential_file_loads(self, loader):
        credentials = MagicMock(valid=True)
        loader.return_value = credentials
        with patch.dict(os.environ, {}, clear=True):
            with tempfile.NamedTemporaryFile() as credential_file:
                self.assertIs(load_credentials(credential_file.name), credentials)
        loader.assert_called_once_with(credential_file.name, READ_ONLY_SCOPES)

    @patch("google.oauth2.credentials.Credentials.from_authorized_user_info")
    @patch("google.oauth2.credentials.Credentials.from_authorized_user_file")
    def test_credential_json_environment_variable_loads(self, file_loader, info_loader):
        credentials = MagicMock(valid=True)
        info_loader.return_value = credentials
        credential_info = {"token": "test-token", "refresh_token": "test-refresh-token"}
        with patch.dict(
            os.environ,
            {"GOOGLE_OAUTH_CREDENTIALS_JSON": json.dumps(credential_info)},
            clear=True,
        ):
            self.assertIs(load_credentials("does-not-exist.json"), credentials)
        info_loader.assert_called_once_with(credential_info, READ_ONLY_SCOPES)
        file_loader.assert_not_called()

    @patch("google.oauth2.credentials.Credentials.from_authorized_user_file", side_effect=ValueError("bad json"))
    def test_invalid_credentials_fail_cleanly(self, _loader):
        with patch.dict(os.environ, {}, clear=True):
            with tempfile.NamedTemporaryFile() as credential_file:
                with self.assertRaises(GoogleAuthError):
                    load_credentials(credential_file.name)

    @patch("google.auth.transport.requests.Request")
    @patch("google.oauth2.credentials.Credentials.from_authorized_user_file")
    def test_expired_credentials_refresh_in_memory(self, loader, request):
        credentials = MagicMock(valid=False, expired=True, refresh_token="refresh-token")
        loader.return_value = credentials
        with patch.dict(os.environ, {}, clear=True):
            with tempfile.NamedTemporaryFile() as credential_file:
                self.assertIs(load_credentials(credential_file.name), credentials)
        credentials.refresh.assert_called_once()

    @patch("google.oauth2.credentials.Credentials.from_authorized_user_file")
    def test_personal_credentials_load_from_personal_path_with_write_scope(self, loader):
        credentials = MagicMock(valid=True)
        loader.return_value = credentials
        with tempfile.NamedTemporaryFile() as credential_file:
            self.assertIs(load_personal_calendar_credentials(credential_file.name), credentials)
        loader.assert_called_once_with(credential_file.name, PERSONAL_CALENDAR_SCOPES)

    def test_personal_credentials_cannot_use_work_credential_file(self):
        with patch.object(config, "SHEILA_PERSONAL_GOOGLE_OAUTH_CREDENTIALS_FILE", config.GOOGLE_OAUTH_CREDENTIALS_FILE):
            with self.assertRaises(GoogleAuthError):
                load_personal_calendar_credentials()

    @patch("integrations.google_auth._build_service")
    def test_work_and_personal_service_builders_use_distinct_loaders(self, builder):
        build_service("calendar", "v3")
        self.assertIs(builder.call_args.args[2], load_credentials)
        build_personal_calendar_service()
        self.assertIs(builder.call_args.args[2], load_personal_calendar_credentials)


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
        zone = config.get_sheila_timezone()
        self.assertEqual(kwargs["timeMin"], start.astimezone(zone).isoformat())
        self.assertEqual(kwargs["timeMax"], (start + timedelta(days=1)).astimezone(zone).isoformat())

    def test_utc_event_is_normalized_to_sheila_timezone_with_dst(self):
        with patch.object(calendar.config, "SHEILA_TIMEZONE", "America/New_York"):
            summer = calendar.normalize_event({"start": {"dateTime": "2026-07-01T13:00:00Z"}, "end": {"dateTime": "2026-07-01T14:00:00Z"}})
            winter = calendar.normalize_event({"start": {"dateTime": "2026-01-01T14:00:00Z"}, "end": {"dateTime": "2026-01-01T15:00:00Z"}})
        self.assertEqual(summer["start"], "2026-07-01T09:00:00-04:00")
        self.assertEqual(winter["start"], "2026-01-01T09:00:00-05:00")

    def test_all_day_event_keeps_date_only_value(self):
        event = calendar.normalize_event({"start": {"date": "2026-08-24"}, "end": {"date": "2026-08-25"}})
        self.assertEqual(event["start"], "2026-08-24")
        self.assertEqual(event["end"], "2026-08-25")

    @patch("integrations.calendar.build_personal_calendar_service")
    def test_personal_adapter_uses_personal_service(self, personal_service):
        service = MagicMock()
        service.events().list().execute.return_value = {"items": []}
        personal_service.return_value = service
        adapter = calendar.GoogleCalendarAdapter("woodysc7@gmail.com")
        result = adapter.list_events(datetime(2026, 8, 24, tzinfo=timezone.utc), datetime(2026, 8, 25, tzinfo=timezone.utc))
        self.assertTrue(result.success)
        personal_service.assert_called_once_with("calendar", "v3")

    @patch("integrations.calendar.build_personal_calendar_service")
    @patch("integrations.calendar.build_service")
    def test_work_helper_uses_work_service_not_personal_service(self, work_service, personal_service):
        service = MagicMock()
        service.events().list().execute.return_value = {"items": []}
        work_service.return_value = service
        calendar.get_events(datetime(2026, 8, 24, tzinfo=timezone.utc), datetime(2026, 8, 25, tzinfo=timezone.utc))
        work_service.assert_called_once_with("calendar", "v3")
        personal_service.assert_not_called()


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
