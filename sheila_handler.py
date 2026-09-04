"""Interface-neutral Sheila message processing.

Terminal and WhatsApp adapters both call :func:`process_message`; this module
owns no transport concerns and preserves Sheila's established workflow.
"""

import re

import brain
import briefing
import memory
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


def process_message(user_text: str) -> str:
    """Process and log one user message through Sheila's existing workflow."""
    lowered = user_text.lower()
    route = route_request(user_text)
    explicit_memory = _extract_explicit_memory(user_text)
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
        followup_match = FOLLOWUP_PATTERN.search(user_text)
        reply = _handle_followup(followup_match.group(1).strip()) if followup_match and not route.capability else str(handle_request(user_text, response_handler=brain.think)["response"])
    if any(phrase in lowered for phrase in FORGET_PHRASES):
        memory.forget_last()
    memory.log_exchange(user_text, reply, important=any(phrase in lowered for phrase in REMEMBER_PHRASES))
    return reply
