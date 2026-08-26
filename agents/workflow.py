"""The first LangGraph workflow above the existing brain layer."""

from collections.abc import Callable
from datetime import date, datetime, time, timedelta
import re
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .router import route_request
from integrations import asana, calendar, drive, gmail
from integrations.google_auth import GoogleAuthError


class SheilaWorkflowState(TypedDict, total=False):
    user_text: str
    route: dict[str, object]
    response_handler: Callable[[str], str]
    response: str
    google_context: str
    unavailable_response: str
    asana_direct_response: str


def routing_node(state: SheilaWorkflowState) -> dict[str, object]:
    """Classify the request without invoking a specialist."""
    return {"route": route_request(state["user_text"]).to_dict()}


def sheila_response_node(state: SheilaWorkflowState) -> dict[str, str]:
    """Use the established brain layer while planned specialists are unavailable."""
    decision = state["route"]
    context = state.get("google_context", "")
    response = state["response_handler"](state["user_text"], context=context) if context else state["response_handler"](state["user_text"])
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
    now = now or datetime.now().astimezone()
    start = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)
    text = user_text.lower()
    if "today" in text and "tomorrow" in text:
        return start, start + timedelta(days=2)
    if "tomorrow" in text:
        start += timedelta(days=1)
    else:
        weekdays = {
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6,
        }
        requested_weekday = next((day for day in weekdays if day in text), None)
        if requested_weekday:
            start += timedelta(days=(weekdays[requested_weekday] - start.weekday()) % 7)
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


def _is_due_today_task(task: dict[str, object], current_date: date) -> bool:
    if task.get("completed"):
        return False
    if task.get("due_on") == current_date.isoformat():
        return True
    due_at = task.get("due_at")
    if isinstance(due_at, str) and due_at:
        try:
            return datetime.fromisoformat(due_at.replace("Z", "+00:00")).astimezone().date() == current_date
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
        if capability == "gmail":
            messages = gmail.search_messages(_gmail_query(user_text), limit=10)
            lines = [f"- From: {m['sender']} | Subject: {m['subject']} | Date: {m['date']} | Preview: {m['snippet'] or m['body'][:500]}" for m in messages]
            return {"google_context": "[GMAIL RESULTS]\n" + ("\n".join(lines) if lines else "No matching messages found.")}
        if capability == "calendar":
            start, end = _calendar_range(user_text)
            events = calendar.get_events(start, end, limit=20)
            lines = [f"- {e['start']} to {e['end']}: {e['title']}" + (f" ({e['location']})" if e['location'] else "") for e in events]
            status = f"Status: successful with {len(events)} event(s). [CALENDAR RESULTS] is authoritative for this range."
            return {"google_context": f"[CALENDAR RESULTS]\nRange: {start.isoformat()} through {end.isoformat()}\n{status}\n" + ("\n".join(lines) if lines else "No events found.")}
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
            today = datetime.now().astimezone().date()
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


def _after_google_data(state: SheilaWorkflowState) -> str:
    if state.get("unavailable_response"):
        return "unavailable"
    return "asana_response" if state.get("asana_direct_response") else "respond"


def build_workflow():
    """Build the minimal request -> route -> Sheila fallback -> final graph."""
    graph = StateGraph(SheilaWorkflowState)
    graph.add_node("route", routing_node)
    graph.add_node("google_data", google_data_node)
    graph.add_node("google_unavailable", google_unavailable_node)
    graph.add_node("asana_response", asana_response_node)
    graph.add_node("sheila_response", sheila_response_node)
    graph.add_edge(START, "route")
    graph.add_conditional_edges("route", lambda state: "google_data" if state["route"].get("capability") else "sheila_response")
    graph.add_conditional_edges("google_data", _after_google_data, {"respond": "sheila_response", "unavailable": "google_unavailable", "asana_response": "asana_response"})
    graph.add_edge("sheila_response", END)
    graph.add_edge("google_unavailable", END)
    graph.add_edge("asana_response", END)
    return graph.compile()


_workflow = build_workflow()


def handle_request(user_text: str, response_handler: Callable[[str], str]) -> dict[str, object]:
    """Run a conversational request through Sheila's orchestration layer.

    ``response_handler`` is supplied by the caller so ``brain.py`` remains the
    only model/fallback layer.
    """
    return _workflow.invoke({"user_text": user_text, "response_handler": response_handler})
