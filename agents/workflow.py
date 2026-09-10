"""The first LangGraph workflow above the existing brain layer."""

from collections.abc import Callable
from datetime import date, datetime, time, timedelta
import re
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

import config
from .router import route_request
from integrations import asana, calendar, drive, gmail
from integrations.google_auth import GoogleAuthError
import personal_calendar
import sheila_tasks


class SheilaWorkflowState(TypedDict, total=False):
    user_text: str
    route: dict[str, object]
    response_handler: Callable[[str], str]
    response: str
    google_context: str
    unavailable_response: str
    asana_direct_response: str
    personal_calendar_response: str
    sheila_task_response: str
    operational_response: str
    calendar_response: str


def routing_node(state: SheilaWorkflowState) -> dict[str, object]:
    """Classify the request without invoking a specialist."""
    return {"route": route_request(state["user_text"]).to_dict()}


def _unverified_calendar_mutation_response(user_text: str, response: str) -> str:
    """Do not let an LLM fallback turn a missed calendar route into a false write."""
    if not personal_calendar.extract_definite_event_candidates(user_text):
        return response
    if re.search(r"\b(?:added|created|scheduled|booked|put)\b.*\b(?:calendar|event)\b|\b(?:i(?:'ve| have)|i am|i'm)\s+adding\b", response, re.IGNORECASE):
        return "I couldn't confirm that calendar event was added."
    return response


def sheila_response_node(state: SheilaWorkflowState) -> dict[str, str]:
    """Use the established brain layer while planned specialists are unavailable."""
    decision = state["route"]
    context = state.get("google_context", "")
    response = state["response_handler"](state["user_text"], context=context) if context else state["response_handler"](state["user_text"])
    response = _unverified_calendar_mutation_response(state["user_text"], response)
    if decision["agent"] != "Sheila":
        response = (
            f"I've identified {decision['agent']} as the right future specialist for this. "
            f"Until that agent is implemented, I'll handle it myself. {response}"
        )
    return {"response": response}


def _gmail_date_constraint(text: str, current_date: date) -> str:
    """Build bounded Gmail date operators from natural-language recency."""
    explicit_operators = re.findall(r"\b(?:after|before|older|newer|newer_than|older_than):\S+", text)
    if explicit_operators:
        return " ".join(explicit_operators)
    if "yesterday" in text:
        start, end = current_date - timedelta(days=1), current_date
    elif "today" in text:
        start, end = current_date, current_date + timedelta(days=1)
    elif "this week" in text:
        start = current_date - timedelta(days=current_date.weekday())
        end = start + timedelta(days=7)
    elif "recent" in text:
        start, end = current_date - timedelta(days=7), current_date + timedelta(days=1)
    else:
        return ""
    return f"after:{start:%Y/%m/%d} before:{end:%Y/%m/%d}"


def _gmail_query(user_text: str, current_date: date | None = None) -> str:
    text = user_text.lower()
    parts: list[str] = []
    if "important" in text:
        parts.append("is:important")
    if "inbox" in text or (not any(marker in text for marker in ("about ", "what did")) and not re.search(r"\bfrom[:\s]", text)):
        parts.append("in:inbox")
    sender_match = re.search(r"\bfrom[:\s]+([\w@.+-]+)", text)
    if not sender_match:
        sender_match = re.search(r"what did\s+([\w .'-]+?)\s+say(?:\s+in\s+(?:their|his|her)\s+email)?\??$", text)
    if not sender_match:
        sender_match = re.search(r"what did\s+([\w .'-]+?)\s+email(?:\s+me)?", text)
    if sender_match:
        parts.append(f"from:{sender_match.group(1).strip('. ')}")
    for marker in ("about ", "anything about "):
        if marker in text:
            search_term = user_text.lower().split(marker, 1)[1]
            search_term = re.sub(r"\b(?:today|yesterday|this week|recent|recently)\b", "", search_term)
            parts.append(search_term.strip(" ?."))
            break
    date_constraint = _gmail_date_constraint(text, current_date or datetime.now().astimezone().date())
    if date_constraint:
        parts.append(date_constraint)
    return " ".join(dict.fromkeys(part for part in parts if part)) or "in:inbox"


def _drive_query(user_text: str) -> str:
    text = user_text.lower()
    folder_match = re.search(r"\b(?:in|from)\s+(?:the\s+)?(.+?)\s+folder\b", text)
    if folder_match:
        return folder_match.group(1).strip(" ?.")
    relationship_match = re.search(r"\bdoes\s+(.+?)\s+serve\b", text)
    if relationship_match:
        return relationship_match.group(1).strip(" ?.")
    for marker in ("about ", "for "):
        if marker in text:
            return user_text.lower().split(marker, 1)[1].strip(" ?.")
    for phrase in ("search my drive", "find my document", "find my file", "what files do i have", "read the document"):
        text = text.replace(phrase, "")
    return text.strip(" ?. ") or user_text


def _calendar_range(user_text: str, now: datetime | None = None) -> tuple[datetime, datetime]:
    timezone = config.get_sheila_timezone()
    now = (now or datetime.now(timezone)).astimezone(timezone)
    start = datetime.combine(now.date(), time.min, tzinfo=timezone)
    text = user_text.lower()
    if "today" in text and "tomorrow" in text:
        return start, start + timedelta(days=2)
    if "tomorrow" in text:
        start += timedelta(days=1)
    else:
        resolved = personal_calendar.resolve_calendar_date(user_text, now)
        if resolved is not None:
            start = datetime.combine(resolved, time.min, tzinfo=timezone)
    if "afternoon" in text:
        return start.replace(hour=12), start.replace(hour=17)
    if "morning" in text:
        return start.replace(hour=8), start.replace(hour=12)
    if "evening" in text:
        return start.replace(hour=17), start.replace(hour=21)
    if "week" in text or "free" in text:
        return start, start + timedelta(days=7)
    return start, start + timedelta(days=1)


def _should_read_drive_document(user_text: str) -> bool:
    text = user_text.lower()
    return any(phrase in text for phrase in ("read ", "what does", "what do", "say about"))


def _is_client_relationship_question(user_text: str) -> bool:
    text = user_text.lower()
    return any(phrase in text for phrase in ("which clients", "what clients", "who are our clients", "companies are clients", "does team networth serve", "clients does"))


def _timed_calendar_events(events: list[dict[str, str]]) -> list[dict[str, str]]:
    return [event for event in events if len(str(event.get("start", ""))) > 10 and len(str(event.get("end", ""))) > 10]


def _format_work_events(events: list[dict[str, str]]) -> str:
    timed = _timed_calendar_events(events)
    lines = [f"- {event['start']} to {event['end']}: {event['title']}" + (f" ({event['location']})" if event.get("location") else "") for event in timed]
    return "[WORK CALENDAR RESULTS]\n" + ("\n".join(lines) if lines else "No timed work events found.")


def _format_personal_events(events: list[dict[str, object]]) -> str:
    lines = [f"- {event['start']} to {event['end']}: {event['title']}" for event in events]
    return "[PERSONAL CALENDAR RESULTS]\n" + ("\n".join(lines) if lines else "No personal events found.")


def _label_personal_response(response: str) -> str:
    if response.startswith("Personal calendar:\n"):
        return "[PERSONAL CALENDAR RESULTS]\n" + response.split("\n", 1)[1]
    return "[PERSONAL CALENDAR RESULTS]\n" + response


def _event_datetime(value: object) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(config.get_sheila_timezone())


def _human_time(value: datetime) -> str:
    if value.hour == 0 and value.minute == 0:
        return "midnight"
    if value.hour == 12 and value.minute == 0:
        return "noon"
    return value.strftime("%I:%M %p").lstrip("0").replace(":00 ", " ")


def _human_day(start: datetime, user_text: str) -> str:
    lowered = user_text.lower()
    if "today" in lowered:
        return "today"
    if "tomorrow" in lowered:
        return "tomorrow"
    return start.strftime("%A")


def _human_title(title: object) -> str:
    cleaned = re.sub(r"[\s.?!]+$", "", str(title)).strip()
    coming = re.match(r"^(.+?)\s+coming$", cleaned, re.IGNORECASE)
    return f"{coming.group(1)} is coming" if coming else cleaned


def _join_phrases(items: list[str]) -> str:
    if len(items) <= 1:
        return items[0] if items else ""
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _conversational_calendar_response(user_text: str, personal: list[dict[str, object]], work: list[dict[str, str]]) -> str:
    timed_work = _timed_calendar_events(work)
    personal_items = [(event, _event_datetime(event["start"])) for event in personal]
    work_items = [(event, _event_datetime(event["start"])) for event in timed_work]
    all_items = sorted(personal_items + work_items, key=lambda item: item[1])
    day = _human_day(all_items[0][1] if all_items else _calendar_range(user_text)[0], user_text)
    work_only = bool(re.search(r"\bwork\b", user_text, re.IGNORECASE))
    if not all_items:
        if re.search(r"\b(?:free|available)\b", user_text, re.IGNORECASE):
            return f"You're free {day}."
        return f"You have nothing scheduled {day}."
    phrases = [f"{_human_title(event['title'])} at {_human_time(start)}" for event, start in all_items if not work_only or event in timed_work]
    if work_only:
        return f"You have {_join_phrases(phrases)}."
    return f"{day.capitalize()} you have {_join_phrases(phrases)}."


def _should_use_conversational_calendar_response(user_text: str) -> bool:
    lowered = user_text.lower()
    return bool(
        re.search(r"\b(?:what do i have|what am i doing|what(?:'s| is) happening|coming up)\b", lowered)
        or re.search(r"\bwork\s+(?:meetings?|calendar)\b", lowered)
        or re.search(r"\b(?:free|available)\b", lowered)
        or "this week" in lowered
        or "this weekend" in lowered
    )


def _named_personal_response(user_text: str, now: datetime | None = None) -> str | None:
    match = re.search(r"\bwhen is\s+(.+?)(?:\?|$)", user_text, re.IGNORECASE)
    if not match:
        return None
    current = (now or datetime.now(config.get_sheila_timezone())).astimezone(config.get_sheila_timezone())
    events = [event for event in personal_calendar.get_personal_calendar_events(current - timedelta(days=366), current + timedelta(days=366))
              if match.group(1).strip().lower() in str(event["title"]).lower()]
    if not events:
        return "I couldn't find that personal event."
    event = events[0]
    start = _event_datetime(event["start"])
    return f"{_human_title(event['title'])} {_human_day(start, user_text)} at {_human_time(start)}."


def _is_natural_calendar_lookup(user_text: str) -> bool:
    return bool(re.search(r"\b(?:what do i have|what am i doing|what(?:'s| is) happening|coming up)\b", user_text, re.IGNORECASE))


def _operational_summary(user_text: str) -> str:
    start, end = _calendar_range(user_text)
    due_date = start.date().isoformat()
    tasks = sheila_tasks.list_tasks(due_date=due_date)
    personal = personal_calendar.get_personal_calendar_events(start, end)
    try:
        work = _timed_calendar_events(calendar.get_events(start, end, limit=20))
    except GoogleAuthError:
        work = []
    sections = ["[REMINDERS / TASKS]\n" + ("\n".join(f"- {task['text']} ({task['due_at'] or task['due_date']})" for task in tasks) if tasks else "No pending reminders due in this range."),
                _format_personal_events(personal), _format_work_events(work)]
    return "\n\n".join(sections)


def _is_due_today_task(task: dict[str, object], current_date: date) -> bool:
    if task.get("completed"):
        return False
    if task.get("due_on") == current_date.isoformat():
        return True
    due_at = task.get("due_at")
    if isinstance(due_at, str) and due_at:
        try:
            return datetime.fromisoformat(due_at.replace("Z", "+00:00")).astimezone(config.get_sheila_timezone()).date() == current_date
        except ValueError:
            return False
    return False


def _is_due_task_request(user_text: str) -> bool:
    """Treat an unspecified 'what is due in Asana' as a due-today question."""
    text = user_text.lower()
    return "due" in text and "overdue" not in text


def _format_asana_task(task: dict[str, object]) -> str:
    return (f"- Name: {task.get('name', '(unnamed task)')} | Due on: {task.get('due_on') or ''} | "
            f"Due at: {task.get('due_at') or ''} | Completed: {task.get('completed', False)} | "
            f"Assignee: {task.get('assignee') or ''} | Project: {task.get('project') or ''} | "
            f"Workspace: {task.get('workspace') or ''} | Link: {task.get('permalink_url') or ''}")


def google_data_node(state: SheilaWorkflowState) -> dict[str, str]:
    """Retrieve a bounded amount of read-only data before Sheila responds."""
    capability = state["route"].get("capability")
    user_text = state["user_text"]
    try:
        if capability == "personal_calendar":
            response = personal_calendar.handle_personal_calendar_request(user_text)
            if _is_natural_calendar_lookup(user_text) and "personal calendar" not in user_text.lower():
                start, end = _calendar_range(user_text)
                try:
                    work = calendar.get_events(start, end, limit=20)
                except GoogleAuthError:
                    work = []
                personal = personal_calendar.get_personal_calendar_events(start, end)
                response = _named_personal_response(user_text) or _conversational_calendar_response(user_text, personal, work)
            return {"personal_calendar_response": response}
        if capability == "sheila_task":
            return {"sheila_task_response": sheila_tasks.handle_request(user_text)}
        if capability == "operational_summary":
            return {"operational_response": _operational_summary(user_text)}
        if capability == "gmail":
            messages = gmail.search_messages(_gmail_query(user_text), limit=10)
            lines = [f"- From: {m['sender']} | Subject: {m['subject']} | Date: {m['date']} | Preview: {m['snippet'] or m['body'][:500]}" for m in messages]
            return {"google_context": "[GMAIL RESULTS]\n" + ("\n".join(lines) if lines else "No matching messages found.")}
        if capability == "calendar":
            start, end = _calendar_range(user_text)
            events = calendar.get_events(start, end, limit=20)
            personal = personal_calendar.get_personal_calendar_events(start, end)
            timed_events = _timed_calendar_events(events)
            work_context = _format_work_events(timed_events)
            status = f"Range: {start.isoformat()} through {end.isoformat()}. Status: successful with {len(timed_events)} event(s). [CALENDAR RESULTS] is authoritative for this range."
            result = {"google_context": f"[CALENDAR RESULTS]\n{status}\n{work_context}\n\n{_format_personal_events(personal)}"}
            if _should_use_conversational_calendar_response(user_text):
                result["calendar_response"] = _conversational_calendar_response(user_text, personal, timed_events)
            return result
        if capability == "drive":
            files = drive.search_files(_drive_query(user_text), limit=10)
            lines = [f"- {f['name']} | {f['mime_type']} | modified {f['modified_time']} | id {f['id']}" for f in files]
            if _should_read_drive_document(user_text) and files:
                document_text = drive.read_google_document(files[0]["id"])
                lines.append(f"\nReadable content from {files[0]['name']}:\n{document_text}")
            relationship_note = ""
            if _is_client_relationship_question(user_text):
                relationship_note = ("\n[DRIVE RELATIONSHIP CAUTION]\n"
                    "These search results establish only file/document associations or mentions. "
                    "They do not confirm a company is a current client unless the retrieved text explicitly says client, account, or customer.")
            return {"google_context": "[DRIVE RESULTS]\n" + ("\n".join(lines) if lines else "No matching files found.") + relationship_note}
        if capability == "asana":
            today = datetime.now(config.get_sheila_timezone()).date()
            if _is_due_task_request(user_text):
                tasks = [task for task in asana.get_tasks(limit=20) if _is_due_today_task(task, today)]
                description = "tasks due today"
            else:
                tasks = asana.get_overdue_tasks(limit=20, current_date=today)
                description = "overdue tasks"
            status = f"Status: successful with {len(tasks)} matching {description}." if tasks else f"Status: successful with zero matching {description}."
            context = f"[ASANA RESULTS]\n{status}\n" + ("\n".join(_format_asana_task(task) for task in tasks) if tasks else "No matching tasks.")
            # Due-date questions are fully answered by structured Asana records.
            # Return that evidence directly so model memory cannot contradict it.
            if _is_due_task_request(user_text):
                return {"google_context": context, "asana_direct_response": context}
            return {"google_context": context}
    except GoogleAuthError:
        unavailable = {"gmail": "Google email isn't available right now.", "calendar": "Google Calendar isn't available right now.", "drive": "Google Drive isn't available right now."}
        response = unavailable.get(capability, "")
        return {"google_context": response, "unavailable_response": response}
    except asana.AsanaError:
        response = "Asana isn't available right now."
        return {"google_context": "[ASANA RESULTS]\nStatus: unavailable because Asana authentication or API retrieval failed.", "unavailable_response": response}
    return {}


def google_unavailable_node(state: SheilaWorkflowState) -> dict[str, str]:
    return {"response": state.get("unavailable_response", state["google_context"])}


def asana_response_node(state: SheilaWorkflowState) -> dict[str, str]:
    """Return deterministic due-date records without an intervening LLM call."""
    return {"response": state["asana_direct_response"]}


def personal_calendar_response_node(state: SheilaWorkflowState) -> dict[str, str]:
    return {"response": state["personal_calendar_response"]}


def calendar_response_node(state: SheilaWorkflowState) -> dict[str, str]:
    return {"response": state["calendar_response"]}


def sheila_task_response_node(state: SheilaWorkflowState) -> dict[str, str]:
    return {"response": state["sheila_task_response"]}


def operational_response_node(state: SheilaWorkflowState) -> dict[str, str]:
    return {"response": state["operational_response"]}


def _after_google_data(state: SheilaWorkflowState) -> str:
    if state.get("unavailable_response"):
        return "unavailable"
    if state.get("personal_calendar_response"):
        return "personal_calendar_response"
    if state.get("calendar_response"):
        return "calendar_response"
    if state.get("sheila_task_response"):
        return "sheila_task_response"
    if state.get("operational_response"):
        return "operational_response"
    return "asana_response" if state.get("asana_direct_response") else "respond"


def build_workflow():
    """Build the minimal request -> route -> Sheila fallback -> final graph."""
    graph = StateGraph(SheilaWorkflowState)
    graph.add_node("route", routing_node)
    graph.add_node("google_data", google_data_node)
    graph.add_node("google_unavailable", google_unavailable_node)
    graph.add_node("asana_response", asana_response_node)
    graph.add_node("personal_calendar_response", personal_calendar_response_node)
    graph.add_node("calendar_response", calendar_response_node)
    graph.add_node("sheila_task_response", sheila_task_response_node)
    graph.add_node("operational_response", operational_response_node)
    graph.add_node("sheila_response", sheila_response_node)
    graph.add_edge(START, "route")
    graph.add_conditional_edges("route", lambda state: "google_data" if state["route"].get("capability") else "sheila_response")
    graph.add_conditional_edges("google_data", _after_google_data, {"respond": "sheila_response", "unavailable": "google_unavailable", "asana_response": "asana_response", "personal_calendar_response": "personal_calendar_response", "calendar_response": "calendar_response", "sheila_task_response": "sheila_task_response", "operational_response": "operational_response"})
    graph.add_edge("sheila_response", END)
    graph.add_edge("google_unavailable", END)
    graph.add_edge("asana_response", END)
    graph.add_edge("personal_calendar_response", END)
    graph.add_edge("calendar_response", END)
    graph.add_edge("sheila_task_response", END)
    graph.add_edge("operational_response", END)
    return graph.compile()


_workflow = build_workflow()


def handle_request(user_text: str, response_handler: Callable[[str], str]) -> dict[str, object]:
    """Run a conversational request through Sheila's orchestration layer.

    ``response_handler`` is supplied by the caller so ``brain.py`` remains the
    only model/fallback layer.
    """
    return _workflow.invoke({"user_text": user_text, "response_handler": response_handler})
