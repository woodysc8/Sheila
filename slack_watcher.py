"""
Iris Slack watcher.

Notifies on:
  - Any direct message
  - @mentions of you in any channel you're a member of
  - Any message in a channel listed in config.SLACK_WATCH_CHANNELS
  - Any message containing a keyword from config.SLACK_KEYWORDS

Same quiet hours / meeting rules as email (via scheduler.py) -- notifications
outside those windows get silently queued instead of interrupting you.

Run this in its OWN terminal window, alongside main.py and email_watcher.py.
Ctrl+C to stop.

Setup: see README.md for the Slack app / token steps.
"""

import time
import requests
from plyer import notification

import config
import tts
import scheduler
import memory

API = "https://slack.com/api"
_headers = {"Authorization": f"Bearer {config.SLACK_USER_TOKEN}"}

_user_cache = {}  # user_id -> display name, avoids re-fetching every time


def _slack_get(endpoint: str, params: dict) -> dict:
    r = requests.get(f"{API}/{endpoint}", headers=_headers, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack API error on {endpoint}: {data.get('error')}")
    return data


def _get_self_id() -> str:
    cached = memory.get_state("slack_self_id")
    if cached:
        return cached
    data = _slack_get("auth.test", {})
    self_id = data["user_id"]
    memory.set_state("slack_self_id", self_id)
    return self_id


def _get_user_name(user_id: str) -> str:
    if user_id in _user_cache:
        return _user_cache[user_id]
    try:
        data = _slack_get("users.info", {"user": user_id})
        name = data["user"].get("real_name") or data["user"].get("name") or user_id
    except Exception:
        name = user_id
    _user_cache[user_id] = name
    return name


def _get_last_ts(channel_id: str) -> str:
    stored = memory.get_state(f"slack_last_ts:{channel_id}")
    if stored:
        return stored
    # First time seeing this channel -- start from now, don't flood with history
    now_ts = str(time.time())
    memory.set_state(f"slack_last_ts:{channel_id}", now_ts)
    return now_ts


def _humanize(sender_name: str, channel_label: str) -> str:
    return f"{sender_name} messaged you in {channel_label} on Slack."


def _notify(sender_name: str, channel_label: str, text: str):
    human = _humanize(sender_name, channel_label)
    memory.log_slack_message(sender_name, channel_label, text)
    memory.queue_notification("slack", human)  # always available for "catch me up"

    if scheduler.should_notify_now():
        print(f"[slack] IMPORTANT -- silent popup: {human}")
        safe_title = human[:60] + ("…" if len(human) > 60 else "")
        try:
            notification.notify(title="Iris", message=safe_title, timeout=15)
        except Exception as e:
            print(f"[slack] Desktop notification failed (non-fatal): {e}")
        if config.SPEAK_NOTIFICATIONS_ALOUD:
            tts.speak(human)
    else:
        reason = "in a meeting" if scheduler.is_in_meeting() else "quiet hours"
        print(f"[slack] Queued ({reason}): {human}")


def _process_channel(channel_id: str, channel_label: str, self_id: str, is_dm: bool, watched: bool):
    last_ts = _get_last_ts(channel_id)
    data = _slack_get("conversations.history", {"channel": channel_id, "oldest": last_ts, "limit": 50})
    messages = data.get("messages", [])
    if not messages:
        return

    newest_ts = last_ts
    # Slack returns newest-first -- process oldest-first so notifications read in order
    for msg in reversed(messages):
        ts = msg.get("ts", "0")
        if float(ts) <= float(last_ts):
            continue
        newest_ts = max(newest_ts, ts, key=float)

        msg_user = msg.get("user")
        if not msg_user or msg_user == self_id:
            continue  # skip your own messages / system messages

        text = msg.get("text", "")
        mentioned = f"<@{self_id}>" in text
        keyword_hit = any(kw.lower() in text.lower() for kw in config.SLACK_KEYWORDS)

        should_notify = is_dm or mentioned or watched or keyword_hit
        if should_notify:
            sender_name = _get_user_name(msg_user)
            _notify(sender_name, channel_label, text)

    memory.set_state(f"slack_last_ts:{channel_id}", newest_ts)


def check_slack():
    self_id = _get_self_id()

    # --- DMs: notify on everything ---
    dm_data = _slack_get("conversations.list", {"types": "im", "limit": 200})
    for convo in dm_data.get("channels", []):
        other_user = convo.get("user")
        label = f"a DM from {_get_user_name(other_user)}" if other_user else "a DM"
        _process_channel(convo["id"], label, self_id, is_dm=True, watched=False)

    # --- Channels you're a member of: notify on mentions/keywords/watched ---
    ch_data = _slack_get(
        "conversations.list",
        {"types": "public_channel,private_channel", "exclude_archived": "true", "limit": 200},
    )
    for convo in ch_data.get("channels", []):
        if not convo.get("is_member"):
            continue
        name = convo.get("name", convo["id"])
        watched = name in config.SLACK_WATCH_CHANNELS
        _process_channel(convo["id"], f"#{name}", self_id, is_dm=False, watched=watched)


def main():
    memory.init_db()
    if not config.SLACK_USER_TOKEN or "PUT_YOUR" in config.SLACK_USER_TOKEN:
        print("[slack] No SLACK_USER_TOKEN set -- see README for setup. Exiting.")
        return
    print(f"[slack] Watching DMs + mentions + {config.SLACK_WATCH_CHANNELS or 'no watched channels'}. "
          f"Checking every {config.SLACK_CHECK_INTERVAL_SECONDS}s. Ctrl+C to stop.")
    while True:
        try:
            check_slack()
            print(f"[slack] Checked at {time.strftime('%H:%M:%S')} -- nothing new to report.")
        except Exception as e:
            print(f"[slack] Error: {e}")
        time.sleep(config.SLACK_CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
