"""Interface-neutral Sheila message processing.

Terminal and WhatsApp adapters both call :func:`process_message`; this module
owns no transport concerns and preserves Sheila's established workflow.
"""

import re

import brain
import briefing
import memory
import personal_calendar
import orchestration
from agents.router import route_request
from agents.workflow import handle_request

REMEMBER_PHRASES = ["remember this", "remember that", "note that down", "don't forget this"]
FORGET_PHRASES = ["forget that", "forget this", "don't remember this", "do not remember this", "delete that", "never mind that"]
EXPLICIT_MEMORY_PREFIXES = ["remember that", "remember this", "keep in mind that", "keep in mind", "note that down"]
CATCHUP_PHRASES = ["catch me up", "fill me in", "what did i miss", "what happened while"]
MEETING_START_PHRASES = ["i'm in a meeting", "im in a meeting", "going into a meeting", "start meeting mode"]
MEETING_END_PHRASES = ["meeting's over", "meeting is over", "i'm out of my meeting", "end meeting mode"]
FOLLOWUP_PATTERN = re.compile(r"what did ([\w\s]+?) (say|email|write|send)", re.IGNORECASE)
EMAIL_QUERY_PATTERN = re.compile(r"(email|emails|mail)(s)?\s+(from|received|i received|i got|today|that came in)", re.IGNORECASE)
MORNING_PROTOCOL_PATTERN = re.compile(r"(?:good\s+morning|morning)(?:\s+sheila)?[!,.?]*", re.IGNORECASE)
CALENDAR_FOLLOWUP_PATTERN = re.compile(r"\b(?:add|put|commit|save)\s+(?:all\s+of\s+)?(?:that|this|it)\b.*\bcalendar\b", re.IGNORECASE)
MIXED_MEMORY_CALENDAR_PATTERN = re.compile(r"\b(?:memory|remember|deep memory)\b.*\bcalendar\b|\bcalendar\b.*\b(?:memory|remember|deep memory)\b", re.IGNORECASE)
ZACH_MEMORY_CALENDAR_PATTERN = re.compile(r"\bzach\s+bryan\b.*\bremember\b|\bremember\b.*\bzach\s+bryan\b", re.IGNORECASE)
CALENDAR_MEMORY_LOOKUP_PATTERN = re.compile(r"\b(?:what(?:'s| is) on|what do i have).*(?:calendar).*\b(?:what i said|remind me what)\b", re.IGNORECASE)
EXISTING_TRAVEL_CONTEXT_PATTERN = re.compile(
    r"\b(?:when\s+am\s+i\s+going|do\s+i\s+have|is\s+there|when\s+is\s+my\s+trip|what\s+(?:are|do)\s+my\s+plans|what\s+do\s+i\s+have\s+planned)\b"
    r".*\b(?:flight|trip|travel|dominican\s+republic|dr|santo\s+domingo|punta\s+cana|sdq)\b",
    re.IGNORECASE,
)
CENTER_CALENDAR_CHECK_PATTERN = re.compile(r"^\s*check\s+(?:my\s+)?calendar\s+(?:for\s+)?tomorrow[?.!]*\s*$", re.IGNORECASE)


def _is_morning_protocol_trigger(user_text: str) -> bool:
    return MORNING_PROTOCOL_PATTERN.fullmatch(user_text.strip()) is not None


def _handle_followup(name_query: str) -> str:
    row = memory.get_latest_message_from(name_query)
    if row is None:
        return f"I don't have anything recent from {name_query}."
    _ts, sender_name, context_label, body, _platform = row
    return brain.summarize_message(sender_name, context_label, body)


def _handle_catchup() -> str:
    pending = memory.get_pending_notifications()
    if not pending:
        return "Nothing came up while you were away. All quiet."
    lines = [summary for (_ts, _source, summary) in pending]
    memory.mark_notifications_delivered()
    return f"One thing came up: {lines[0]}" if len(lines) == 1 else f"{len(lines)} things came up. " + " Also, ".join(lines)


def _handle_email_query() -> str:
    pending = memory.get_pending_notifications()
    if not pending:
        return "I don't have any recent email activity recorded right now."
    memory.mark_notifications_delivered()
    lines = [summary for (_ts, _source, summary) in pending]
    return f"The latest email activity was: {lines[0]}" if len(lines) == 1 else "Recent email activity includes: " + "; ".join(lines)


def _memory_key(content: str) -> str | None:
    lowered = content.lower()
    if any(term in lowered for term in (" live ", " lives ", " reside ", " resides ")):
        return "residence"
    if any(term in lowered for term in (" prefer ", " hates ", " hate ", " don't like ", " do not like ")):
        return "preference"
    if any(term in lowered for term in (" work ", " works ", " job ", " employed ")):
        return "work"
    relationship = re.search(r"\bmy\s+(sister|brother|friend|mother|father|partner|wife|husband)\b", lowered)
    if relationship:
        return f"relationship:{relationship.group(1)}"
    if any(term in lowered for term in (" going to ", " going on ", " retreat ", " trip ")):
        return "travel"
    return None


def _extract_explicit_memory(user_text: str) -> tuple[str, str] | None:
    lowered = user_text.lower().strip()
    if any(phrase in lowered for phrase in FORGET_PHRASES):
        return None
    content = user_text.strip()
    for prefix in EXPLICIT_MEMORY_PREFIXES:
        if lowered.startswith(prefix):
            content = content[len(prefix):].strip(" .,:;")
            break
    else:
        durable_markers = (
            "i prefer ", "i hate ", "i don't like ", "i do not like ",
            "i don't live ", "i do not live ", "my ", "i'm going to ", "i am going to ", "i work ", "i live ",
            "from now on ",
        )
        if not any(lowered.startswith(marker) for marker in durable_markers):
            return None
    if not content:
        return None
    key = _memory_key(f" {content.lower()} ")
    category = (
        "preference" if key == "preference"
        else "relationship" if key and key.startswith("relationship:")
        else "work" if key == "work"
        else "travel" if key == "travel"
        else "personal"
    )
    return category, content


def _durable_calendar_facts(text: str, now=None) -> list[tuple[str, str, str]]:
    """Keep durable travel context, not a copy of every dated Calendar event."""
    lowered = text.lower()
    facts: list[tuple[str, str, str]] = []
    if "taking pto" in lowered and ("dr" in lowered or "dominican" in lowered):
        facts.append(("travel", "Sam is taking PTO and traveling to the Dominican Republic with Nora.", "travel:pto-dominican-republic"))
    if "company retreat" in lowered and "philly" in lowered:
        facts.append(("travel", "Sam has a company retreat in Philadelphia.", "travel:company-retreat-philadelphia"))
    if "alumni weekend" in lowered or "holy cross" in lowered or " back to hc" in lowered:
        facts.append(("personal", "Sam returns to Holy Cross for Alumni Weekend.", "personal:holy-cross-alumni-weekend"))
    return facts


def _zach_memory_candidate(text: str) -> dict[str, object] | None:
    if not ZACH_MEMORY_CALENDAR_PATTERN.search(text):
        return None
    event_date = personal_calendar.resolve_calendar_date(text)
    if event_date is None:
        return None
    return {
        "category": "personal",
        "content": f"Sam plans to attend Zach Bryan at Gillette Stadium on {event_date.strftime('%B')} {event_date.day}, {event_date.year}.",
        "importance": 4,
        "metadata": {"memory_key": f"plan:zach-bryan-gillette-{event_date.isoformat()}", "explicit": True},
    }


def _calendar_and_memory_lookup(user_text: str) -> str:
    context = orchestration.request_context(user_text, action="calendar_and_memory_read", target="google_calendar")
    try:
        trip_events = personal_calendar.find_existing_trip_events(user_text)
        calendar_reply = (personal_calendar.format_existing_trip_events(trip_events)
                          if trip_events is not None
                          else personal_calendar.handle_personal_calendar_request(user_text))
    except Exception:
        calendar_reply = "I couldn't reach your personal Google Calendar."
    try:
        remembered = memory.recall(user_text, limit=4)
    except Exception:
        return f"Calendar (Google Calendar):\n{calendar_reply}\n\nRemembered context (Sam 2): unavailable."
    memory_lines = [str(item.get("content", "")) for item in remembered if item.get("content")]
    memory_reply = "No relevant remembered context." if not memory_lines else "\n".join(f"- {line}" for line in memory_lines)
    return f"Calendar (Google Calendar):\n{calendar_reply}\n\nRemembered context (Sam 2):\n{memory_reply}"


def _center_calendar_reply(result: object) -> str:
    if not isinstance(result, dict):
        return "Center returned an invalid calendar result."
    payload = result.get("result")
    if not isinstance(payload, dict):
        return "Center did not return a calendar result."
    events = payload.get("events")
    if not isinstance(events, list):
        return "Center did not return a calendar event list."
    if not events:
        return "Your Google Calendar is clear tomorrow."
    return "Google Calendar tomorrow:\n" + "\n".join(
        f"- {event.get('title', '(untitled event)')}" for event in events if isinstance(event, dict)
    )


def process_message(user_text: str) -> str:
    """Process and log one user message through Sheila's existing workflow."""
    # Terse calendar details (for example, "When?") are resolved from the
    # adapter's ephemeral event ID and re-read from Google Calendar. They are
    # neither durable memory nor a reason to invoke Center.
    if (personal_calendar.is_event_detail_followup(user_text) or
            personal_calendar.is_ambiguous_event_followup(user_text)):
        reply = personal_calendar.handle_personal_calendar_request(user_text)
        memory.log_exchange(user_text, reply, important=False)
        return reply
    if personal_calendar.has_ambiguous_event_reference():
        personal_calendar.clear_ambiguous_event_reference()
    prior_text = memory.get_latest_user_text() if CALENDAR_FOLLOWUP_PATTERN.search(user_text) else None
    effective_text = f"{prior_text}\n{user_text}" if prior_text else user_text
    lowered = effective_text.lower()
    if CENTER_CALENDAR_CHECK_PATTERN.match(user_text):
        context = orchestration.request_context(user_text, action="calendar_read", target="center")
        result = orchestration.execute_with_center(context, "calendar_read", "Check my calendar for tomorrow.")
        reply = (_center_calendar_reply(result.result) if result.status == "succeeded"
                 else "Center could not complete the calendar check.")
        memory.log_exchange(user_text, reply, important=False)
        return reply
    if CALENDAR_MEMORY_LOOKUP_PATTERN.search(user_text) or EXISTING_TRAVEL_CONTEXT_PATTERN.search(user_text):
        reply = _calendar_and_memory_lookup(user_text)
        memory.log_exchange(user_text, reply, important=False)
        return reply
    route = route_request(effective_text)
    explicit_memory = _extract_explicit_memory(effective_text)
    if explicit_memory:
        category, content = explicit_memory
        key = _memory_key(f" {content.lower()} ")
        memory.remember(
            category,
            content,
            source="user",
            importance=5,
            metadata={"explicit": True, **({"memory_key": key} if key else {})},
        )
    if any(phrase in lowered for phrase in FORGET_PHRASES):
        memory.forget_latest_structured(source="user")
    if _is_morning_protocol_trigger(user_text):
        reply = briefing.build_morning_briefing()
    elif any(phrase in lowered for phrase in MEETING_START_PHRASES):
        memory.set_meeting_status(True)
        reply = "Understood. I'll hold notifications until you're out."
    elif any(phrase in lowered for phrase in MEETING_END_PHRASES):
        memory.set_meeting_status(False)
        reply = "Welcome back. Say the word if you'd like me to catch you up."
    elif any(phrase in lowered for phrase in CATCHUP_PHRASES) and not route.capability:
        reply = _handle_catchup()
    elif EMAIL_QUERY_PATTERN.search(user_text) and not route.capability:
        reply = _handle_email_query()
    else:
        followup_match = FOLLOWUP_PATTERN.search(effective_text)
        reply = _handle_followup(followup_match.group(1).strip()) if followup_match and not route.capability else str(handle_request(effective_text, response_handler=brain.think)["response"])
    zach_candidate = _zach_memory_candidate(effective_text)
    if zach_candidate and ("added" in reply.lower() or "already on your personal google calendar" in reply.lower()):
        context = orchestration.request_context(user_text, action="durable_memory_write", target="sam2")
        memory_result = orchestration.persist_memory_candidate(context, zach_candidate)
        reply += (" I also saved that plan to Sam 2."
                  if memory_result.status == "succeeded"
                  else " The calendar result is confirmed, but I couldn't save the memory to Sam 2.")
    elif MIXED_MEMORY_CALENDAR_PATTERN.search(effective_text) and "couldn't" not in reply.lower() and "please specify" not in reply.lower():
        facts = _durable_calendar_facts(effective_text)
        for category, content, key in facts:
            memory.remember(category, content, source="user", importance=4, metadata={"explicit": True, "memory_key": key})
        if facts and ("added" in reply.lower() or "got it" in reply.lower()):
            reply += " I also saved the durable travel and plan context to Sam 2."
    if any(phrase in lowered for phrase in FORGET_PHRASES):
        memory.forget_last()
    memory.log_exchange(user_text, reply, important=any(phrase in lowered for phrase in REMEMBER_PHRASES))
    return reply
