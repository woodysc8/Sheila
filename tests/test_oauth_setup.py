import unittest

import config
import oauth_setup


class OAuthSetupProfileTests(unittest.TestCase):
    def test_work_profile_preserves_existing_files_and_read_only_scopes(self):
        profile = oauth_setup._profile("work")
        self.assertEqual(profile["client_file"], ".gauth.json")
        self.assertEqual(profile["credential_file"], ".oauth2.sam@streetcredpr.com.json")
        self.assertEqual(profile["scopes"], oauth_setup.WORK_SCOPES)

    def test_personal_profile_uses_separate_client_token_and_calendar_write_scope(self):
        profile = oauth_setup._profile("personal")
        self.assertEqual(profile["client_file"], config.SHEILA_PERSONAL_GOOGLE_OAUTH_CLIENT_FILE)
        self.assertEqual(profile["credential_file"], config.SHEILA_PERSONAL_GOOGLE_OAUTH_CREDENTIALS_FILE)
        self.assertEqual(profile["scopes"], oauth_setup.PERSONAL_CALENDAR_SCOPES)
        self.assertNotEqual(profile["credential_file"], oauth_setup.CREDENTIAL_FILE)
        self.assertEqual(profile["account"], "woodysc7@gmail.com")


if __name__ == "__main__":
    unittest.main()
