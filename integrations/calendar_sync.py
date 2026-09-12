"""One-way, idempotent mirroring from StreetCred work Calendar to personal Calendar."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import json
import random
import time
from typing import Any, Callable

import config
from .google_auth import build_personal_calendar_service, build_service


MIRROR_SOURCE = "Sheila"
MIRROR_SOURCE_CALENDAR = "work"
MIRROR_PROPERTY_SOURCE = "source"
MIRROR_PROPERTY_CALENDAR = "source_calendar"
MIRROR_PROPERTY_EVENT_ID = "source_event_id"


class CalendarSyncError(RuntimeError):
    """Raised when a one-way Calendar sync cannot safely run."""


@dataclass
class SyncSummary:
    work_events_found: int = 0
    created: int = 0
    updated: int = 0
    deleted: int = 0
    unchanged: int = 0
    cancelled: int = 0

    def as_dict(self) -> dict[str, int]:
        return {"work_events_found": self.work_events_found, "created": self.created,
                "updated": self.updated, "deleted": self.deleted,
                "unchanged": self.unchanged, "cancelled": self.cancelled}


class WorkToPersonalCalendarSync:
    """Sync work events to metadata-marked personal mirrors, never in reverse."""

    def __init__(self, work_service_factory: Callable[[str, str], Any] | None = None,
                 personal_service_factory: Callable[[str, str], Any] | None = None,
                 work_calendar_id: str = "primary", personal_calendar_id: str | None = None,
                 max_retries: int | None = None, backoff_base_seconds: float | None = None,
                 backoff_max_seconds: float | None = None, write_throttle_seconds: float | None = None,
                 sleep_fn: Callable[[float], None] = time.sleep,
                 random_fn: Callable[[], float] = random.random):
        self.work_calendar_id = work_calendar_id
        self.personal_calendar_id = (personal_calendar_id or config.SHEILA_PERSONAL_GOOGLE_CALENDAR_ID).strip()
        self._work_service_factory = work_service_factory or build_service
        self._personal_service_factory = personal_service_factory or build_personal_calendar_service
        self.max_retries = config.SHEILA_CALENDAR_SYNC_MAX_RETRIES if max_retries is None else max(0, max_retries)
        self.backoff_base_seconds = config.SHEILA_CALENDAR_SYNC_BACKOFF_BASE_SECONDS if backoff_base_seconds is None else max(0, backoff_base_seconds)
        self.backoff_max_seconds = config.SHEILA_CALENDAR_SYNC_BACKOFF_MAX_SECONDS if backoff_max_seconds is None else max(0, backoff_max_seconds)
        self.write_throttle_seconds = config.SHEILA_CALENDAR_SYNC_WRITE_THROTTLE_SECONDS if write_throttle_seconds is None else max(0, write_throttle_seconds)
        self._sleep = sleep_fn
        self._random = random_fn
        self._has_successful_write = False

    def run(self, start: datetime, end: datetime, dry_run: bool = False) -> SyncSummary:
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise CalendarSyncError("Sync range must be timezone-aware with an end after its start.")
        if not self.personal_calendar_id:
            raise CalendarSyncError("Personal Google Calendar is not configured. Set SHEILA_PERSONAL_GOOGLE_CALENDAR_ID.")

        work_service = self._work_service_factory("calendar", "v3")
        personal_service = self._personal_service_factory("calendar", "v3")
        self._has_successful_write = False
        work_events = self._list_work_events(work_service, start, end)
        mirrors = self._list_personal_mirrors(personal_service)
        mirrors_by_source = {self._source_event_id(event): event for event in mirrors if self._source_event_id(event)}
        summary = SyncSummary(work_events_found=len(work_events))

        for work_event in work_events:
            source_event_id = work_event.get("id", "")
            if not source_event_id:
                continue
            mirror = mirrors_by_source.get(source_event_id)
            if work_event.get("status") == "cancelled":
                summary.cancelled += 1
                if mirror:
                    summary.deleted += 1
                    if not dry_run:
                        self._write(lambda: personal_service.events().delete(
                            calendarId=self.personal_calendar_id, eventId=mirror["id"]
                        ).execute(), "deleting a personal Calendar mirror")
                continue

            desired = self._mirror_body(work_event)
            if not mirror:
                summary.created += 1
                if not dry_run:
                    self._write(lambda: personal_service.events().insert(
                        calendarId=self.personal_calendar_id, body=desired
                    ).execute(), "creating a personal Calendar mirror")
            elif self._mirror_matches(mirror, desired):
                summary.unchanged += 1
            else:
                summary.updated += 1
                if not dry_run:
                    self._write(lambda: personal_service.events().update(
                        calendarId=self.personal_calendar_id, eventId=mirror["id"], body=desired
                    ).execute(), "updating a personal Calendar mirror")
        return summary

    def _list_work_events(self, service: Any, start: datetime, end: datetime) -> list[dict[str, Any]]:
        timezone = config.get_sheila_timezone()
        return self._paginated_events(service, {
            "calendarId": self.work_calendar_id,
            "timeMin": start.astimezone(timezone).isoformat(),
            "timeMax": end.astimezone(timezone).isoformat(),
            "singleEvents": True,
            "showDeleted": True,
            "maxResults": 2500,
        })

    def _list_personal_mirrors(self, service: Any) -> list[dict[str, Any]]:
        # Query only Sheila-created work mirrors. A second local metadata check
        # protects unrelated personal events even if an API response is broad.
        events = self._paginated_events(service, {
            "calendarId": self.personal_calendar_id,
            "privateExtendedProperty": [
                f"{MIRROR_PROPERTY_SOURCE}={MIRROR_SOURCE}",
                f"{MIRROR_PROPERTY_CALENDAR}={MIRROR_SOURCE_CALENDAR}",
            ],
            "showDeleted": False,
            "maxResults": 2500,
        })
        return [event for event in events if self._is_mirror(event)]

    def _paginated_events(self, service: Any, initial_kwargs: dict[str, Any]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            kwargs = dict(initial_kwargs)
            if page_token:
                kwargs["pageToken"] = page_token
            response = self._execute_with_retry(
                lambda: service.events().list(**kwargs).execute(), "listing Google Calendar events"
            )
            events.extend(response.get("items", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                return events

    def _write(self, operation: Callable[[], Any], operation_label: str) -> Any:
        if self._has_successful_write and self.write_throttle_seconds:
            self._sleep(self.write_throttle_seconds)
        result = self._execute_with_retry(operation, operation_label)
        self._has_successful_write = True
        return result

    def _execute_with_retry(self, operation: Callable[[], Any], operation_label: str) -> Any:
        for attempt in range(self.max_retries + 1):
            try:
                return operation()
            except Exception as exc:
                if not self._is_rate_limit_error(exc):
                    raise
                if attempt == self.max_retries:
                    raise CalendarSyncError(
                        f"Google Calendar rate limiting prevented {operation_label} from completing "
                        f"after {self.max_retries + 1} attempts."
                    ) from exc
                capped_delay = min(self.backoff_max_seconds, self.backoff_base_seconds * (2 ** attempt))
                # Jitter spreads retries from competing sync workers while never
                # exceeding the configured per-retry maximum delay.
                jitter = min(1.0, max(0.0, self._random()))
                delay = capped_delay * (0.5 + (0.5 * jitter))
                self._sleep(delay)

    @staticmethod
    def _is_rate_limit_error(exc: Exception) -> bool:
        response = getattr(exc, "resp", None)
        status = getattr(response, "status", getattr(exc, "status_code", None))
        if status == 429:
            return True
        if status != 403:
            return False
        content = getattr(exc, "content", b"")
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")
        try:
            reasons = [item.get("reason") for item in json.loads(content).get("error", {}).get("errors", [])]
        except (TypeError, ValueError, AttributeError):
            return False
        return "rateLimitExceeded" in reasons or "userRateLimitExceeded" in reasons

    @staticmethod
    def _source_event_id(event: dict[str, Any]) -> str:
        return event.get("extendedProperties", {}).get("private", {}).get(MIRROR_PROPERTY_EVENT_ID, "")

    @classmethod
    def _is_mirror(cls, event: dict[str, Any]) -> bool:
        private = event.get("extendedProperties", {}).get("private", {})
        return (private.get(MIRROR_PROPERTY_SOURCE) == MIRROR_SOURCE
                and private.get(MIRROR_PROPERTY_CALENDAR) == MIRROR_SOURCE_CALENDAR
                and bool(private.get(MIRROR_PROPERTY_EVENT_ID)))

    @staticmethod
    def _mirror_body(work_event: dict[str, Any]) -> dict[str, Any]:
        source_id = work_event["id"]
        private = {MIRROR_PROPERTY_SOURCE: MIRROR_SOURCE,
                   MIRROR_PROPERTY_CALENDAR: MIRROR_SOURCE_CALENDAR,
                   MIRROR_PROPERTY_EVENT_ID: source_id}
        return {"summary": work_event.get("summary", ""),
                "description": work_event.get("description", ""),
                "location": work_event.get("location", ""),
                "start": deepcopy(work_event.get("start", {})),
                "end": deepcopy(work_event.get("end", {})),
                "extendedProperties": {"private": private}}

    @classmethod
    def _mirror_matches(cls, mirror: dict[str, Any], desired: dict[str, Any]) -> bool:
        current = {"summary": mirror.get("summary", ""),
                   "description": mirror.get("description", ""),
                   "location": mirror.get("location", ""),
                   "start": mirror.get("start", {}), "end": mirror.get("end", {}),
                   "extendedProperties": {"private": {
                       key: mirror.get("extendedProperties", {}).get("private", {}).get(key, "")
                       for key in desired["extendedProperties"]["private"]
                   }}}
        return current == desired
