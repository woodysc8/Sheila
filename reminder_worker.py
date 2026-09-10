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


def dispatch_once(now: datetime | None = None, sender=deliver) -> bool:
    current = (now or datetime.now(config.get_sheila_timezone())).astimezone(config.get_sheila_timezone())
    item = operational_store.claim_due(current)
    if not item:
        return False
    try:
        sender(item)
    except Exception as exc:
        operational_store.complete_delivery(item, current, exc)
        return False
    operational_store.complete_delivery(item, current)
    return True


def main() -> None:
    operational_store.initialize()
    while True:
        while dispatch_once():
            pass
        time.sleep(30)


if __name__ == "__main__":
    main()
