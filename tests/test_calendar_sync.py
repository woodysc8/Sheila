import json
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from integrations.calendar_sync import WorkToPersonalCalendarSync


class FakeGoogleHttpError(Exception):
    def __init__(self, status, reason):
        self.resp = type("Response", (), {"status": status})()
        self.content = json.dumps({"error": {"errors": [{"reason": reason}]}}).encode()
        super().__init__(reason)


def work_event(event_id="work-1", **changes):
    event = {"id": event_id, "summary": "Client Meeting", "description": "Discuss launch",
             "location": "Office", "start": {"dateTime": "2026-09-11T13:00:00-04:00", "timeZone": "America/New_York"},
             "end": {"dateTime": "2026-09-11T14:00:00-04:00", "timeZone": "America/New_York"}}
    event.update(changes)
    return event


def mirror_event(source_event, mirror_id="personal-1", **changes):
    event = {"id": mirror_id, "summary": source_event.get("summary", ""),
             "description": source_event.get("description", ""), "location": source_event.get("location", ""),
             "start": source_event["start"], "end": source_event["end"],
             "extendedProperties": {"private": {"source": "Sheila", "source_calendar": "work", "source_event_id": source_event["id"]}}}
    event.update(changes)
    return event


class WorkToPersonalCalendarSyncTests(unittest.TestCase):
    def setUp(self):
        self.work = MagicMock()
        self.personal = MagicMock()
        self.start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.end = datetime(2027, 1, 1, tzinfo=timezone.utc)
        self.sync = WorkToPersonalCalendarSync(lambda *_: self.work, lambda *_: self.personal,
                                               personal_calendar_id="woodysc7@gmail.com")

    def _responses(self, work_events, personal_events):
        self.work.events().list().execute.return_value = {"items": work_events}
        self.personal.events().list().execute.return_value = {"items": personal_events}

    def _with_retry_settings(self, **kwargs):
        kwargs.setdefault("write_throttle_seconds", 0)
        return WorkToPersonalCalendarSync(
            lambda *_: self.work, lambda *_: self.personal,
            personal_calendar_id="woodysc7@gmail.com", **kwargs,
        )

    def test_new_work_event_creates_personal_mirror(self):
        source = work_event()
        self._responses([source], [])
        summary = self.sync.run(self.start, self.end)
        self.assertEqual(summary.created, 1)
        body = self.personal.events().insert.call_args.kwargs["body"]
        self.assertEqual(body["summary"], "Client Meeting")
        self.assertEqual(body["extendedProperties"]["private"]["source_event_id"], "work-1")
        self.work.events().insert.assert_not_called()
        self.work.events().update.assert_not_called()
        self.work.events().delete.assert_not_called()

    def test_existing_mirror_is_not_duplicated_and_repeated_sync_is_idempotent(self):
        source = work_event()
        self._responses([source], [mirror_event(source)])
        first = self.sync.run(self.start, self.end)
        second = self.sync.run(self.start, self.end)
        self.assertEqual(first.unchanged, 1)
        self.assertEqual(second.unchanged, 1)
        self.personal.events().insert.assert_not_called()
        self.personal.events().update.assert_not_called()

    def test_changed_work_event_updates_existing_mirror(self):
        original = work_event()
        changed = work_event(summary="Client Meeting (moved)")
        self._responses([changed], [mirror_event(original)])
        summary = self.sync.run(self.start, self.end)
        self.assertEqual(summary.updated, 1)
        kwargs = self.personal.events().update.call_args.kwargs
        self.assertEqual(kwargs["eventId"], "personal-1")
        self.assertEqual(kwargs["body"]["summary"], "Client Meeting (moved)")

    def test_cancelled_work_event_removes_only_its_mirror(self):
        cancelled = work_event(status="cancelled")
        unrelated = {"id": "personal-unrelated", "summary": "Dentist", "start": {"dateTime": "2026-09-12T09:00:00-04:00"}, "end": {"dateTime": "2026-09-12T10:00:00-04:00"}}
        self._responses([cancelled], [mirror_event(cancelled), unrelated])
        summary = self.sync.run(self.start, self.end)
        self.assertEqual(summary.deleted, 1)
        self.assertEqual(self.personal.events().delete.call_args.kwargs["eventId"], "personal-1")
        deleted_ids = [call.kwargs["eventId"] for call in self.personal.events().delete.call_args_list]
        self.assertNotIn("personal-unrelated", deleted_ids)

    def test_dry_run_reports_changes_without_writes(self):
        source = work_event()
        self._responses([source], [])
        summary = self.sync.run(self.start, self.end, dry_run=True)
        self.assertEqual(summary.created, 1)
        self.personal.events().insert.assert_not_called()
        self.personal.events().update.assert_not_called()
        self.personal.events().delete.assert_not_called()

    def test_403_rate_limit_retries_and_eventually_creates_mirror(self):
        self._responses([work_event()], [])
        sleep = MagicMock()
        self.personal.events.return_value.insert.return_value.execute.side_effect = [
            FakeGoogleHttpError(403, "rateLimitExceeded"), {"id": "personal-1"},
        ]
        sync = self._with_retry_settings(max_retries=2, backoff_base_seconds=2,
                                         backoff_max_seconds=10, sleep_fn=sleep, random_fn=lambda: 0)
        summary = sync.run(self.start, self.end)
        self.assertEqual(summary.created, 1)
        self.assertEqual(self.personal.events.return_value.insert.call_count, 2)
        sleep.assert_called_once_with(1.0)

    def test_429_retries_and_eventually_returns_operation_result(self):
        self._responses([work_event()], [])
        sleep = MagicMock()
        self.personal.events.return_value.insert.return_value.execute.side_effect = [
            FakeGoogleHttpError(429, "tooManyRequests"), {"id": "personal-1"},
        ]
        summary = self._with_retry_settings(max_retries=1, sleep_fn=sleep, random_fn=lambda: 1).run(self.start, self.end)
        self.assertEqual(summary.created, 1)
        self.assertEqual(self.personal.events.return_value.insert.call_count, 2)
        sleep.assert_called_once_with(1.0)

    def test_exhausted_rate_limit_retries_raise_clear_error(self):
        self._responses([work_event()], [])
        sleep = MagicMock()
        self.personal.events.return_value.insert.return_value.execute.side_effect = [
            FakeGoogleHttpError(403, "rateLimitExceeded"), FakeGoogleHttpError(403, "rateLimitExceeded"),
        ]
        sync = self._with_retry_settings(max_retries=1, sleep_fn=sleep, random_fn=lambda: 0)
        with self.assertRaisesRegex(RuntimeError, "rate limiting prevented creating a personal Calendar mirror"):
            sync.run(self.start, self.end)
        self.assertEqual(self.personal.events.return_value.insert.call_count, 2)
        sleep.assert_called_once()

    def test_non_rate_limit_403_is_not_retried(self):
        self._responses([work_event()], [])
        sleep = MagicMock()
        failure = FakeGoogleHttpError(403, "forbidden")
        self.personal.events.return_value.insert.return_value.execute.side_effect = failure
        sync = self._with_retry_settings(max_retries=5, sleep_fn=sleep)
        with self.assertRaises(FakeGoogleHttpError):
            sync.run(self.start, self.end)
        self.assertEqual(self.personal.events.return_value.insert.call_count, 1)
        sleep.assert_not_called()

    def test_successful_writes_are_throttled_with_injectable_sleep(self):
        first, second = work_event("work-1"), work_event("work-2")
        self._responses([first, second], [])
        sleep = MagicMock()
        summary = self._with_retry_settings(write_throttle_seconds=0.25, sleep_fn=sleep).run(self.start, self.end)
        self.assertEqual(summary.created, 2)
        sleep.assert_called_once_with(0.25)

    def test_pagination_reads_every_work_and_personal_page(self):
        first, second = work_event("work-1"), work_event("work-2")
        self.work.events().list().execute.side_effect = [
            {"items": [first], "nextPageToken": "next-work"}, {"items": [second]},
        ]
        self.personal.events().list().execute.side_effect = [
            {"items": [], "nextPageToken": "next-personal"}, {"items": []},
        ]
        self.work.events.return_value.list.reset_mock()
        self.personal.events.return_value.list.reset_mock()
        summary = self.sync.run(self.start, self.end, dry_run=True)
        self.assertEqual(summary.work_events_found, 2)
        self.assertEqual(summary.created, 2)
        self.assertEqual(self.work.events.return_value.list.call_args_list[1].kwargs["pageToken"], "next-work")
        self.assertEqual(self.personal.events.return_value.list.call_args_list[1].kwargs["pageToken"], "next-personal")

    @patch("integrations.calendar_sync.build_personal_calendar_service")
    @patch("integrations.calendar_sync.build_service")
    def test_default_sync_uses_separate_work_and_personal_services(self, work_builder, personal_builder):
        work_builder.return_value = self.work
        personal_builder.return_value = self.personal
        self._responses([], [])
        sync = WorkToPersonalCalendarSync(personal_calendar_id="woodysc7@gmail.com")
        sync.run(self.start, self.end, dry_run=True)
        work_builder.assert_called_once_with("calendar", "v3")
        personal_builder.assert_called_once_with("calendar", "v3")


if __name__ == "__main__":
    unittest.main()
