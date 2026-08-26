"""Shared, read-only authorized-user Google API authentication."""

from pathlib import Path

import config


READ_ONLY_SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
)


class GoogleAuthError(RuntimeError):
    """Raised when Sheila cannot safely use the local Google credentials."""


def load_credentials(credentials_file: str | None = None):
    """Load and refresh the existing authorized-user credentials in memory."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = Path(credentials_file or config.GOOGLE_OAUTH_CREDENTIALS_FILE)
    if not path.is_file():
        raise GoogleAuthError(f"Google credentials file is missing: {path}")
    try:
        credentials = Credentials.from_authorized_user_file(str(path), READ_ONLY_SCOPES)
        if not credentials.valid:
            if credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
            else:
                raise GoogleAuthError("Google credentials are invalid or cannot be refreshed.")
        return credentials
    except GoogleAuthError:
        raise
    except Exception as exc:
        raise GoogleAuthError("Google credentials are invalid or unavailable.") from exc


def build_service(api_name: str, version: str):
    """Return an authenticated API client without requesting any new scopes."""
    from googleapiclient.discovery import build

    try:
        return build(api_name, version, credentials=load_credentials(), cache_discovery=False)
    except GoogleAuthError:
        raise
    except Exception as exc:
        raise GoogleAuthError(f"Google {api_name} is unavailable.") from exc
