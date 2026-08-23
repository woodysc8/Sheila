import os
import re
import requests
from icalendar import Calendar
import config
import memory


def _summarize_with_ai(text: str) -> str | None:
    try:
        from google import genai
        from google.genai import types
    except Exception:
        return None

    api_keys = [k for k in config.GEMINI_API_KEYS if k and "PUT_YOUR" not in k]
    for key in api_keys:
        try:
            client = genai.Client(api_key=key)
            response = client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=f"Summarize the following morning briefing content in 3 short spoken sentences:\n\n{text}",
                config=types.GenerateContentConfig(max_output_tokens=180),
            )
            return response.text.strip()
        except Exception:
            continue
    return None


def get_asana_overdue_tasks() -> list[str]:
    token = os.environ.get("ASANA_PAT", "").strip()
    if not token or "PUT_YOUR" in token.upper():
        return []

    headers = {"Authorization": f"Bearer {token}"}
    urls = [
        "https://app.asana.com/api/1.0/tasks?assignee=me&completed_since=now&opt_fields=name",
        "https://app.asana.com/api/1.0/tasks?assignee=me&opt_fields=name",
    ]

    for url in urls:
        try:
            r = requests.get(url, headers=headers, timeout=20)
            if r.status_code != 200:
                continue
            tasks = []
            for item in r.json().get("data", []):
                if item.get("name"):
                    tasks.append(item["name"])
            return tasks[:8]
        except Exception:
            continue

    return []


def get_todays_calendar_events() -> list[str]:
    ics_url = os.environ.get("CALENDAR_ICS_URL", config.CALENDAR_ICS_URL).strip()
    if not ics_url or "PUT_YOUR" in ics_url:
        fallback_email = config.EMAIL_ADDRESS.strip()
        if "@" in fallback_email:
            ics_url = f"https://calendar.google.com/calendar/ical/{fallback_email}/public/basic.ics"
        else:
            return []

    ics_url = ics_url.replace("/basic.i", "/basic.ics")
    if "public/basic.ics" not in ics_url and "calendar/ical/" in ics_url:
        ics_url = ics_url.rstrip("/") + "/public/basic.ics"

    try:
        r = requests.get(ics_url, timeout=20)
        if r.status_code != 200:
            return []
        cal = Calendar.from_ical(r.text)
        events = []
        for component in cal.walk("VEVENT"):
            summary = str(component.get("summary", ""))
            if summary:
                events.append(summary)
        return events[:8]
    except Exception:
        return []


def get_weather_summary() -> str:
    try:
        r = requests.get("https://wttr.in/Providence,RI?format=j1", timeout=20)
        r.raise_for_status()
        data = r.json()
        current = data.get("current_condition", [{}])[0]
        temp_f = int(float(current.get("temp_F", 0)))
        weather_desc = current.get("weatherDesc", [{}])[0].get("value", "unclear")
        if "rain" in weather_desc.lower():
            return f"Providence is {weather_desc.lower()}, high around {temp_f}°F, with a chance of rain later."
        return f"Providence is {weather_desc.lower()}, high around {temp_f}°F."
    except Exception as e:
        print(f"[briefing] Weather lookup failed: {e}")
        return "Weather unavailable."


def _extract_market_threads(text: str) -> list[str]:
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if len(line) > 40 or "morning brew" in line.lower() or "market" in line.lower():
            lines.append(line)
    return lines[:6]


def _get_morning_brew_digest() -> str:
    recent = memory.get_pending_notifications()
    if not recent:
        return ""
    entries = []
    for _, source, summary in recent:
        if "brew" in source.lower() or "market" in summary.lower() or "morning brew" in summary.lower():
            entries.append(summary)
    return "\n".join(entries)


def _thread_key_from_summary(summary: str) -> str:
    match = re.search(r"in the (.+?) thread", summary, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return summary.strip()


def _group_emails_by_thread(items: list[tuple]) -> list[str]:
    grouped: dict[str, list[str]] = {}
    for _, source, summary in items:
        if source.lower() != "email":
            continue
        thread_key = _thread_key_from_summary(summary)
        grouped.setdefault(thread_key, []).append(summary)

    lines = []
    for thread, entries in grouped.items():
        unique_entries = []
        for entry in entries:
            if entry not in unique_entries:
                unique_entries.append(entry)
        lines.append(f"- {thread}: {', '.join(unique_entries[:3])}")
    return lines[:8]


def build_morning_briefing() -> str:
    pending = memory.get_pending_notifications()
    email_items = [(ts, source, summary) for ts, source, summary in pending if source.lower() == "email"]
    market_lines = _extract_market_threads(_get_morning_brew_digest())

    asana_tasks = get_asana_overdue_tasks()
    calendar_events = get_todays_calendar_events()
    weather = get_weather_summary()

    parts = []
    parts.append("Recent email activity since your last check:")
    if email_items:
        parts.extend(_group_emails_by_thread(email_items))
    else:
        parts.append("- No recent email activity recorded yet.")

    parts.append("\nOverdue Asana tasks:")
    if asana_tasks:
        parts.extend([f"- {task}" for task in asana_tasks])
    else:
        parts.append("- No overdue Asana tasks.")

    parts.append("\nToday's calendar events:")
    if calendar_events:
        parts.extend([f"- {event}" for event in calendar_events])
    else:
        parts.append("- No calendar events found.")

    parts.append(f"\nWeather: {weather}")

    if market_lines:
        parts.append("\nMorning Brew / market threads:")
        parts.extend([f"- {line}" for line in market_lines])
    else:
        parts.append("\nMorning Brew / market threads: none detected.")

    memory.mark_notifications_delivered()
    body = "\n".join(parts)
    ai_summary = _summarize_with_ai(body)
    if ai_summary:
        return ai_summary

    return body


if __name__ == "__main__":
    print(build_morning_briefing())
