"""CLI for a bounded one-way work-to-personal Calendar synchronization."""

import argparse
from datetime import date, datetime, time

import config
from integrations.calendar_sync import CalendarSyncError, WorkToPersonalCalendarSync


def _range_bound(value: str) -> datetime:
    try:
        return datetime.combine(date.fromisoformat(value), time.min, config.get_sheila_timezone())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("dates must use YYYY-MM-DD") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="One-way work-to-personal Google Calendar sync.")
    parser.add_argument("--start", required=True, type=_range_bound, help="Inclusive YYYY-MM-DD")
    parser.add_argument("--end", required=True, type=_range_bound, help="Exclusive YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing to Google Calendar")
    args = parser.parse_args(argv)
    try:
        summary = WorkToPersonalCalendarSync().run(args.start, args.end, dry_run=args.dry_run)
    except CalendarSyncError as exc:
        parser.error(str(exc))
    for label, count in summary.as_dict().items():
        print(f"{label}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
