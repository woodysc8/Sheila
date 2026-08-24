import os
import unittest
from unittest.mock import MagicMock, patch
import ingest


class IngestTests(unittest.TestCase):
    def test_collect_files_includes_shared_drive_path(self):
        with patch("ingest.config.KNOWLEDGE_DIRS", []), \
             patch("ingest.config.SHARED_DRIVE_PATH", "C:/tmp/shared"), \
             patch("ingest.os.path.isdir", return_value=True), \
             patch("os.walk", return_value=[("C:/tmp/shared", [], ["foo.txt"])]):
            files = ingest._collect_files()
            self.assertEqual(files, [os.path.join("C:/tmp/shared", "foo.txt")])

    @patch("ingest._drive_api_available", return_value=True)
    def test_collect_files_prefixes_live_drive_results(self, _drive_available):
        with patch("ingest.config.GOOGLE_DRIVE_FOLDER_ID", "folder123"), \
             patch("ingest._list_google_drive_files", return_value=["drive://doc-1/file.txt"]):
            files = ingest._collect_files()
            self.assertIn("drive://doc-1/file.txt", files)

    @patch("google.oauth2.service_account.Credentials.from_service_account_file", return_value=object())
    @patch("googleapiclient.discovery.build", return_value=object())
    def test_get_drive_service_uses_google_service_account_env(self, build_mock, creds_mock):
        with patch.dict("ingest.os.environ", {"GOOGLE_SERVICE_ACCOUNT": "service-account.json"}, clear=False):
            service = ingest._get_drive_service()
            self.assertIsNotNone(service)
            creds_mock.assert_called_once()
            build_mock.assert_called_once()

    @patch("ingest._drive_api_available", return_value=True)
    def test_list_google_drive_files_passes_shared_drive_flags(self, _drive_available):
        fake_service = MagicMock()
        fake_service.files().get.return_value.execute.return_value = {"id": "folder123", "name": "Shared", "mimeType": "application/vnd.google-apps.folder"}
        fake_service.files().list.return_value.execute.return_value = {"files": []}

        with patch("ingest._get_drive_service", return_value=fake_service):
            result = ingest._list_google_drive_files("folder123")

        self.assertEqual(result, [])
        fake_service.files().list.assert_called_once_with(
            q="'folder123' in parents and trashed = false",
            pageSize=1000,
            fields="nextPageToken, files(id, name, mimeType, parents)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )


if __name__ == "__main__":
    unittest.main()
