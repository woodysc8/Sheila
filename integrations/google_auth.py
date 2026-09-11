"""Separate authorized-user Google authentication for work and personal Calendar."""

import json
import os
from pathlib import Path

import config


# Preserve the StreetCred/work permissions exactly as they were.
READ_ONLY_SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
)
PERSONAL_CALENDAR_SCOPES = ("https://www.googleapis.com/auth/calendar",)


class GoogleAuthError(RuntimeError):
    """Raised when Sheila cannot safely use local Google credentials."""


def _load_authorized_credentials(credentials_file: str, scopes: tuple[str, ...],
                                 credentials_json: str | None = None):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    try:
        if credentials_json:
            credentials = Credentials.from_authorized_user_info(json.loads(credentials_json), scopes)
        else:
            path = Path(credentials_file)
            if not path.is_file():
                raise GoogleAuthError(f"Google credentials file is missing: {path}")
            credentials = Credentials.from_authorized_user_file(str(path), scopes)
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


def load_credentials(credentials_file: str | None = None):
    """Load StreetCred/work credentials; preserved for existing integrations."""
    return _load_authorized_credentials(
        credentials_file or config.GOOGLE_OAUTH_CREDENTIALS_FILE,
        READ_ONLY_SCOPES,
        os.environ.get("GOOGLE_OAUTH_CREDENTIALS_JSON"),
    )


def load_personal_calendar_credentials(credentials_file: str | None = None):
    """Load only the dedicated personal Calendar authorized-user token."""
    path = credentials_file or config.SHEILA_PERSONAL_GOOGLE_OAUTH_CREDENTIALS_FILE
    if Path(path).resolve() == Path(config.GOOGLE_OAUTH_CREDENTIALS_FILE).resolve():
        raise GoogleAuthError("Personal Calendar credentials must use a separate file from work credentials.")
    return _load_authorized_credentials(path, PERSONAL_CALENDAR_SCOPES)


def _build_service(api_name: str, version: str, credentials_loader):
    from googleapiclient.discovery import build
    try:
        return build(api_name, version, credentials=credentials_loader(), cache_discovery=False)
    except GoogleAuthError:
        raise
    except Exception as exc:
        raise GoogleAuthError(f"Google {api_name} is unavailable.") from exc


def build_service(api_name: str, version: str):
    """Build a service using the original StreetCred/work credential set."""
    return _build_service(api_name, version, load_credentials)


def build_personal_calendar_service(api_name: str = "calendar", version: str = "v3"):
    """Build a Calendar service using only the personal credential set."""
    if api_name != "calendar":
        raise GoogleAuthError("Personal OAuth credentials are limited to Google Calendar.")
    return _build_service(api_name, version, load_personal_calendar_credentials)
