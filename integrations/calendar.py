"""Read-only Google Calendar helpers."""

from datetime import datetime

import config
from .google_auth import GoogleAuthError, build_service


def _user_datetime(value: str) -> str:
    if not value or len(value) == 10:
        return value
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(config.get_sheila_timezone()).isoformat()
    except ValueError:
        return value


def normalize_event(event: dict) -> dict[str, str]:
    return {"id": event.get("id", ""), "calendar_id": event.get("organizer", {}).get("email", "primary"),
            "title": event.get("summary", "(untitled event)"), "start": _user_datetime(event.get("start", {}).get("dateTime", event.get("start", {}).get("date", ""))),
            "end": _user_datetime(event.get("end", {}).get("dateTime", event.get("end", {}).get("date", ""))),
            "location": event.get("location", ""), "description": event.get("description", "")[:1500]}


def list_calendars() -> list[dict[str, str]]:
    try:
        return [{"id": c.get("id", ""), "summary": c.get("summary", ""), "primary": str(c.get("primary", False))}
                for c in build_service("calendar", "v3").calendarList().list().execute().get("items", [])]
    except GoogleAuthError:
        raise
    except Exception as exc:
        raise GoogleAuthError("Calendar listing failed.") from exc


def get_events(time_min: datetime, time_max: datetime, limit: int = 20) -> list[dict[str, str]]:
    """Get relevant primary-calendar events in an inclusive ISO-8601 range."""
    timezone = config.get_sheila_timezone()
    if time_min.tzinfo is None or time_max.tzinfo is None:
        raise ValueError("Calendar query ranges must be timezone-aware.")
    time_min = time_min.astimezone(timezone)
    time_max = time_max.astimezone(timezone)
    try:
        events = build_service("calendar", "v3").events().list(calendarId="primary", timeMin=time_min.isoformat(), timeMax=time_max.isoformat(),
            singleEvents=True, orderBy="startTime", maxResults=min(max(limit, 1), 20)).execute().get("items", [])
        return [normalize_event(event) for event in events]
    except GoogleAuthError:
        raise
    except Exception as exc:
        raise GoogleAuthError("Calendar event retrieval failed.") from exc
