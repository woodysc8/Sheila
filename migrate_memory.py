"""Safely plan or explicitly apply Sheila structured-memory migration."""

import argparse
import json
from typing import Any

import memory
from second_brain import SecondBrainClient, SecondBrainError


REMOTE_MEMORY_PATH = "/api/memories"


def load_local_memories() -> list[dict]:
    """Read all local structured memories without changing their rows."""
    memory.init_db()
    conn = memory._connect()
    try:
        rows = conn.execute(
            "SELECT * FROM structured_memories ORDER BY id ASC"
        ).fetchall()
        return [memory._memory_row(row) for row in rows]
    finally:
        conn.close()


def local_memory_payload(record: dict) -> dict:
    """Map one local row to the Sam 2 memory request shape.

    Args:
        record: A normalized row from Sheila's structured memory table.

    Returns:
        A JSON-serializable Sam 2 memory payload.

    Raises:
        ValueError: If the row cannot satisfy the Sam 2 memory schema.
    """
    required_text = ("category", "content", "source")
    for field in required_text:
        value = record.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")

    importance = record.get("importance")
    if isinstance(importance, bool) or not isinstance(importance, int) or not 0 <= importance <= 100:
        raise ValueError("importance must be an integer from 0 to 100")

    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object")

    memory_key = metadata.get("memory_key")
    if memory_key is not None and (not isinstance(memory_key, str) or not memory_key.strip()):
        raise ValueError("metadata.memory_key must be a non-empty string or null")

    source_id = record.get("source_id")
    if source_id is not None and not isinstance(source_id, str):
        raise ValueError("source_id must be a string or null")

    return {
        "category": record["category"],
        "content": record["content"],
        "source": record["source"],
        "source_id": source_id,
        "importance": importance,
        "memory_key": memory_key,
        "metadata": metadata,
    }


def _operation(action: str, record: dict, payload: dict) -> dict:
    return {
        "action": action,
        "method": "POST",
        "path": REMOTE_MEMORY_PATH,
        "local_id": record.get("id"),
        "memory_key": payload.get("memory_key"),
        "payload": payload,
    }


def build_plan(records: list[dict], client: Any = None) -> dict:
    """Build a migration report, using only remote reads for planning.

    Args:
        records: Local structured-memory rows.
        client: Existing SecondBrainClient-compatible object. It is only used
            for keyed search during planning and for writes during apply.

    Returns:
        A report containing validation, collision, skip, and operation details.
    """
    report = {
        "total_local_memories": len(records),
        "valid_memories": 0,
        "invalid_memories": [],
        "duplicate_key_collisions": [],
        "would_create": [],
        "would_replace": [],
        "skipped": [],
        "remote_lookup_errors": [],
        "remote_operations": [],
    }
    seen_keys: dict[str, int] = {}

    for record in records:
        local_id = record.get("id")
        try:
            payload = local_memory_payload(record)
        except (TypeError, ValueError) as exc:
            report["invalid_memories"].append({"local_id": local_id, "reason": str(exc)})
            report["skipped"].append({"local_id": local_id, "reason": "invalid_record"})
            continue

        report["valid_memories"] += 1
        memory_key = payload["memory_key"]
        if memory_key is not None:
            if memory_key in seen_keys:
                collision = {
                    "memory_key": memory_key,
                    "local_id": local_id,
                    "kept_local_id": seen_keys[memory_key],
                }
                report["duplicate_key_collisions"].append(collision)
                report["skipped"].append({
                    "local_id": local_id,
                    "reason": "duplicate_local_memory_key",
                    "memory_key": memory_key,
                })
                continue
            seen_keys[memory_key] = local_id

        existing = []
        if memory_key is not None:
            if client is None:
                report["remote_lookup_errors"].append({
                    "local_id": local_id,
                    "reason": "client_not_configured",
                })
                report["skipped"].append({
                    "local_id": local_id,
                    "reason": "remote_key_lookup_unavailable",
                    "memory_key": memory_key,
                })
                continue
            try:
                existing = client.recall(memory_key=memory_key, limit=1)
            except SecondBrainError as exc:
                report["remote_lookup_errors"].append({
                    "local_id": local_id,
                    "reason": str(exc),
                })
                report["skipped"].append({
                    "local_id": local_id,
                    "reason": "remote_key_lookup_failed",
                    "memory_key": memory_key,
                })
                continue

        action = "replace" if existing else "create"
        operation = _operation(action, record, payload)
        report["remote_operations"].append(operation)
        report["would_replace" if action == "replace" else "would_create"].append(operation)

    return report


def apply_plan(report: dict, client: SecondBrainClient) -> dict:
    """Apply planned POST operations and report individual API failures."""
    report = dict(report)
    report["applied"] = []
    report["apply_errors"] = []
    for operation in report["remote_operations"]:
        try:
            payload = operation["payload"]
            response = client.remember(
                payload["category"],
                payload["content"],
                payload["source"],
                payload["source_id"],
                payload["importance"],
                payload["metadata"],
            )
            report["applied"].append({
                "local_id": operation["local_id"],
                "action": operation["action"],
                "remote_id": response.get("id"),
            })
        except SecondBrainError as exc:
            report["apply_errors"].append({
                "local_id": operation["local_id"],
                "action": operation["action"],
                "reason": str(exc),
            })
    return report


def migrate(dry_run: bool = True, client: SecondBrainClient = None) -> dict:
    """Plan a migration, applying writes only when explicitly requested."""
    client = client or SecondBrainClient()
    records = load_local_memories()
    report = build_plan(records, client=client)
    report["dry_run"] = dry_run
    if not dry_run:
        report = apply_plan(report, client)
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--dry-run",
        action="store_true",
        help="plan without remote writes (the default)",
    )
    modes.add_argument(
        "--apply",
        action="store_true",
        help="explicitly POST planned memories to Sam 2",
    )
    return parser


def main() -> int:
    """Print a migration plan or explicit apply result as JSON."""
    args = _parser().parse_args()
    dry_run = not args.apply
    try:
        report = migrate(dry_run=dry_run)
    except SecondBrainError as exc:
        report = {
            "dry_run": dry_run,
            "fatal_error": str(exc),
        }
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not report.get("apply_errors") else 1


if __name__ == "__main__":
    raise SystemExit(main())
