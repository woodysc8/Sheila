"""Opt-in SQLite operational-state migration. Never touches memory tables."""
import argparse
from datetime import datetime
import sqlite3
from pathlib import Path

import operational_store


def migrate(source_path: str, apply: bool = False) -> dict[str, int]:
    report = {"found": 0, "would_migrate": 0, "migrated": 0, "skipped": 0, "errors": 0}
    # Immutable source connection: migration never creates, updates, or deletes
    # anything in the legacy memory database.
    source_uri = Path(source_path).resolve().as_uri() + "?mode=ro"
    source = sqlite3.connect(source_uri, uri=True); source.row_factory = sqlite3.Row
    try:
        for table in ("calendar_events", "sheila_tasks"):
            exists = source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            if not exists: continue
            for row in source.execute(f"SELECT * FROM {table}"):
                report["found"] += 1; key = f"sqlite:{source_path}:{table}:{row['id']}"
                if apply and operational_store.migrated(key): report["skipped"] += 1; continue
                report["would_migrate"] += 1
                if not apply: continue
                try:
                    if table == "calendar_events":
                        from datetime import datetime as dt
                        operational_store.create_calendar_event(row["title"], dt.fromisoformat(row["start_at"]), dt.fromisoformat(row["end_at"]), row["description"], row["timezone"], row["location"])
                    elif row["status"] == "pending" and row["due_at"]:
                        operational_store.create_reminder(row["text"], datetime.fromisoformat(row["due_at"]), "America/New_York")
                    else: report["skipped"] += 1; continue
                    operational_store.mark_migrated(key); report["migrated"] += 1
                except Exception: report["errors"] += 1
    finally: source.close()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("source_sqlite"); parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(); print(migrate(args.source_sqlite, args.apply))
