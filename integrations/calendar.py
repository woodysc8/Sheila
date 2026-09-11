"""Google Calendar access for Sheila's separate work and personal calendars."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable

import config
from .google_auth import GoogleAuthError, build_personal_calendar_service, build_service

PERSONAL_CALENDAR_ID_ENV = "SHEILA_PERSONAL_GOOGLE_CALENDAR_ID"


@dataclass(frozen=True)
class CalendarResult:
    """Outcome of a Calendar operation; mutations succeed only after execute()."""
    success: bool
    value: Any = None
    error: str | None = None


def _user_datetime(value: str) -> str:
    if not value or len(value) == 10:
        return value
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(config.get_sheila_timezone()).isoformat()
    except ValueError:
        return value


def normalize_event(event: dict[str, Any], calendar_id: str | None = None) -> dict[str, Any]:
    """Map Google events to Sheila's stable representation.

    All-day end dates stay exclusive, as specified by the Google Calendar API.
    Timed values are always rendered in Sheila's America/New_York timezone.
    """
    start, end = event.get("start", {}), event.get("end", {})
    all_day = "date" in start
    return {"id": event.get("id", ""), "calendar_id": calendar_id or event.get("organizer", {}).get("email", "primary"),
            "title": event.get("summary", "(untitled event)"),
            "start": _user_datetime(start.get("date") if all_day else start.get("dateTime", "")),
            "end": _user_datetime(end.get("date") if "date" in end else end.get("dateTime", "")),
            "all_day": all_day, "timezone": config.SHEILA_TIMEZONE if not all_day else None,
            "location": event.get("location", ""), "description": event.get("description", "")[:1500]}


def _as_user_datetime(value: str | datetime | date) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Timed event datetimes must be ISO 8601.") from exc
    else:
        raise ValueError("Timed events require datetime start and end values.")
    timezone = config.get_sheila_timezone()
    return parsed.replace(tzinfo=timezone) if parsed.tzinfo is None else parsed.astimezone(timezone)


def _event_body(title: str, start: str | datetime | date, end: str | datetime | date,
                description: str = "", location: str = "") -> dict[str, Any]:
    if not title or not title.strip():
        raise ValueError("Event title is required.")
    start_is_date = isinstance(start, date) and not isinstance(start, datetime)
    end_is_date = isinstance(end, date) and not isinstance(end, datetime)
    if start_is_date != end_is_date:
        raise ValueError("Event start and end must both be all-day dates or timed datetimes.")
    if start_is_date:
        if end <= start:
            raise ValueError("All-day event end date must be after its start date.")
        start_data, end_data = {"date": start.isoformat()}, {"date": end.isoformat()}
    else:
        start_dt, end_dt = _as_user_datetime(start), _as_user_datetime(end)
        if end_dt <= start_dt:
            raise ValueError("Event end must be after its start.")
        start_data = {"dateTime": start_dt.isoformat(), "timeZone": config.SHEILA_TIMEZONE}
        end_data = {"dateTime": end_dt.isoformat(), "timeZone": config.SHEILA_TIMEZONE}
    body: dict[str, Any] = {"summary": title.strip(), "start": start_data, "end": end_data}
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    return body


class GoogleCalendarAdapter:
    """Read/write adapter for the explicitly configured personal calendar."""
    def __init__(self, calendar_id: str | None = None, service_factory: Callable[[str, str], Any] | None = None):
        self.calendar_id = (calendar_id if calendar_id is not None else config.SHEILA_PERSONAL_GOOGLE_CALENDAR_ID).strip()
        self._service_factory = service_factory or build_personal_calendar_service

    def _service(self) -> CalendarResult:
        if not self.calendar_id:
            return CalendarResult(False, error=(f"Personal Google Calendar is not configured. Set {PERSONAL_CALENDAR_ID_ENV} to the target calendar ID."))
        try:
            return CalendarResult(True, self._service_factory("calendar", "v3"))
        except Exception as exc:
            return CalendarResult(False, error=f"Google Calendar authentication failed: {exc}")

    def list_events(self, time_min: datetime, time_max: datetime, limit: int = 20) -> CalendarResult:
        if time_min.tzinfo is None or time_max.tzinfo is None or time_max <= time_min:
            return CalendarResult(False, error="Calendar query range must be timezone-aware with an end after its start.")
        service = self._service()
        if not service.success:
            return service
        timezone = config.get_sheila_timezone()
        try:
            items = service.value.events().list(calendarId=self.calendar_id, timeMin=time_min.astimezone(timezone).isoformat(), timeMax=time_max.astimezone(timezone).isoformat(), singleEvents=True, orderBy="startTime", maxResults=min(max(limit, 1), 2500)).execute().get("items", [])
            return CalendarResult(True, [normalize_event(event, self.calendar_id) for event in items])
        except Exception as exc:
            return CalendarResult(False, error=f"Google Calendar event retrieval failed: {exc}")

    def find_event(self, event_id: str) -> CalendarResult:
        if not event_id:
            return CalendarResult(False, error="Google Calendar event ID is required.")
        service = self._service()
        if not service.success:
            return service
        try:
            event = service.value.events().get(calendarId=self.calendar_id, eventId=event_id).execute()
            return CalendarResult(True, normalize_event(event, self.calendar_id))
        except Exception as exc:
            return CalendarResult(False, error=f"Google Calendar event lookup failed: {exc}")

    def create_event(self, title: str, start: str | datetime | date, end: str | datetime | date, description: str = "", location: str = "") -> CalendarResult:
        try:
            body = _event_body(title, start, end, description, location)
        except ValueError as exc:
            return CalendarResult(False, error=str(exc))
        service = self._service()
        if not service.success:
            return service
        try:
            event = service.value.events().insert(calendarId=self.calendar_id, body=body).execute()
            return CalendarResult(True, normalize_event(event, self.calendar_id))
        except Exception as exc:
            return CalendarResult(False, error=f"Google Calendar event creation failed: {exc}")

    def update_event(self, event_id: str, title: str, start: str | datetime | date, end: str | datetime | date, description: str = "", location: str = "") -> CalendarResult:
        if not event_id:
            return CalendarResult(False, error="Google Calendar event ID is required.")
        try:
            body = _event_body(title, start, end, description, location)
        except ValueError as exc:
            return CalendarResult(False, error=str(exc))
        service = self._service()
        if not service.success:
            return service
        try:
            event = service.value.events().update(calendarId=self.calendar_id, eventId=event_id, body=body).execute()
            return CalendarResult(True, normalize_event(event, self.calendar_id))
        except Exception as exc:
            return CalendarResult(False, error=f"Google Calendar event update failed: {exc}")

    def delete_event(self, event_id: str) -> CalendarResult:
        if not event_id:
            return CalendarResult(False, error="Google Calendar event ID is required.")
        service = self._service()
        if not service.success:
            return service
        try:
            service.value.events().delete(calendarId=self.calendar_id, eventId=event_id).execute()
            return CalendarResult(True, True)
        except Exception as exc:
            return CalendarResult(False, error=f"Google Calendar event deletion failed: {exc}")

    def list_calendars(self) -> CalendarResult:
        try:
            # Discovery does not target a personal calendar, so it remains
            # available to help configure the required calendar ID.
            service = self._service_factory("calendar", "v3")
            items = service.calendarList().list().execute().get("items", [])
            return CalendarResult(True, [{"id": item.get("id", ""), "summary": item.get("summary", ""), "primary": bool(item.get("primary", False))} for item in items])
        except Exception as exc:
            return CalendarResult(False, error=f"Google Calendar listing failed: {exc}")


# Existing work-calendar helpers retain their prior primary-calendar behavior
# and, importantly, the StreetCred/work credential loader.
def list_calendars() -> list[dict[str, Any]]:
    try:
        items = build_service("calendar", "v3").calendarList().list().execute().get("items", [])
        return [{"id": item.get("id", ""), "summary": item.get("summary", ""),
                 "primary": bool(item.get("primary", False))} for item in items]
    except GoogleAuthError:
        raise
    except Exception as exc:
        raise GoogleAuthError("Calendar listing failed.") from exc


def get_events(time_min: datetime, time_max: datetime, limit: int = 20) -> list[dict[str, Any]]:
    if time_min.tzinfo is None or time_max.tzinfo is None:
        raise ValueError("Calendar query ranges must be timezone-aware.")
    timezone = config.get_sheila_timezone()
    try:
        items = build_service("calendar", "v3").events().list(
            calendarId="primary", timeMin=time_min.astimezone(timezone).isoformat(),
            timeMax=time_max.astimezone(timezone).isoformat(), singleEvents=True,
            orderBy="startTime", maxResults=min(max(limit, 1), 20),
        ).execute().get("items", [])
        return [normalize_event(event) for event in items]
    except GoogleAuthError:
        raise
    except Exception as exc:
        raise GoogleAuthError("Calendar event retrieval failed.") from exc
