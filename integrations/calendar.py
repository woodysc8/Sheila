"""Read-only Google Calendar helpers."""

from datetime import datetime

from .google_auth import GoogleAuthError, build_service


def normalize_event(event: dict) -> dict[str, str]:
    return {"id": event.get("id", ""), "calendar_id": event.get("organizer", {}).get("email", "primary"),
            "title": event.get("summary", "(untitled event)"), "start": event.get("start", {}).get("dateTime", event.get("start", {}).get("date", "")),
            "end": event.get("end", {}).get("dateTime", event.get("end", {}).get("date", "")),
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
    try:
        events = build_service("calendar", "v3").events().list(calendarId="primary", timeMin=time_min.isoformat(), timeMax=time_max.isoformat(),
            singleEvents=True, orderBy="startTime", maxResults=min(max(limit, 1), 20)).execute().get("items", [])
        return [normalize_event(event) for event in events]
    except GoogleAuthError:
        raise
    except Exception as exc:
        raise GoogleAuthError("Calendar event retrieval failed.") from exc
