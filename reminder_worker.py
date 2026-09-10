"""Run as Sheila's single Render background worker: ``python reminder_worker.py``."""
from datetime import datetime
import time

import config
from integrations import zavu
import operational_store


def deliver(reminder: dict) -> dict:
    """Small explicit adapter around Sheila's existing outbound Zavu sender."""
    if not config.SHEILA_REMINDER_RECIPIENT:
        raise RuntimeError("SHEILA_REMINDER_RECIPIENT is not configured")
    return zavu.send_text(config.SHEILA_REMINDER_RECIPIENT, config.SHEILA_REMINDER_CHANNEL,
                          f"Reminder: {reminder['text']}", config.SHEILA_REMINDER_SENDER_ID)


def dispatch_due(now: datetime | None = None, sender=deliver, limit: int = 100) -> dict[str, int]:
    """Process due occurrences using the store's atomic claim as the lock."""
    current = (now or datetime.now(config.get_sheila_timezone())).astimezone(config.get_sheila_timezone())
    result = {"processed": 0, "sent": 0, "failed": 0}
    while result["processed"] < limit:
        item = operational_store.claim_due(current)
        if not item:
            break
        result["processed"] += 1
        try:
            sender(item)
        except Exception as exc:
            operational_store.complete_delivery(item, current, exc)
            result["failed"] += 1
        else:
            operational_store.complete_delivery(item, current)
            result["sent"] += 1
    return result


def dispatch_once(now: datetime | None = None, sender=deliver) -> bool:
    """Compatibility wrapper for callers/tests that process at most one item."""
    return dispatch_due(now, sender, limit=1)["sent"] == 1


def main() -> None:
    operational_store.initialize()
    while True:
        dispatch_due()
        time.sleep(30)


if __name__ == "__main__":
    main()
