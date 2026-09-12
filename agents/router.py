"""Local intent routing for Sheila's present and future specialists.

Routing is intentionally deterministic for now. It does not create another
model client or attempt to call planned agents.
"""

from dataclasses import asdict, dataclass
import re


@dataclass(frozen=True)
class RoutingDecision:
    agent: str
    intent: str
    delegation_ready: bool
    reason: str
    capability: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


FINANCIAL_TERMS = (
    "tax", "taxes", "tax return", "deduction", "deductions", "irs",
    "financial", "financial plan", "investment", "investments", "portfolio", "budget",
    "retirement", "401k", "ira", "capital gains",
)
TRAVEL_TERMS = (
    "travel", "trip", "flight", "flights", "hotel", "itinerary",
    "vacation", "holiday", "airbnb", "airport", "destination",
)
TRAVEL_OPERATION_PATTERN = re.compile(
    r"\b(?:search|find|compare|book|recommend|build|plan|change|modify|cancel)\b.*\b(?:flight|flights|hotel|hotels|trip|travel|itinerary)\b",
    re.IGNORECASE,
)
EXISTING_TRAVEL_INFO_PATTERN = re.compile(
    r"\b(?:do\s+i\s+have|when\s+am\s+i\s+going|is\s+there|what\s+(?:flight|trip)|when\s+is\s+my\s+trip|what\s+(?:are|do)\s+my\s+plans|what\s+do\s+i\s+have\s+planned)\b"
    r".*\b(?:flight|trip|travel|dominican\s+republic|dr|santo\s+domingo|punta\s+cana|sdq)\b",
    re.IGNORECASE,
)
RESEARCH_TERMS = (
    "research", "deep research", "research report", "analyze",
    "analysis", "compare", "presentation", "briefing deck", "market study",
)
GMAIL_TERMS = ("email", "emails", "mail", "inbox", "gmail")
CALENDAR_TERMS = ("calendar", "meeting", "meetings", "agenda", "when am i free", "when i'm free", "am i free")
PERSONAL_CALENDAR_TERMS = ("personal calendar", "my personal calendar")
PERSONAL_CALENDAR_COMMAND_PATTERN = re.compile(
    r"(?:\b(?:cancel|delete|remove)\b.*|\b(?:is|are)\b.*\b(?:off|cancelled|canceled|anymore)\b|\b(?:add|schedule|put|create|book|move|reschedule|change|update)\b"
    r".*\b(?:today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|at|to)\b)",
    re.IGNORECASE,
)
PERSONAL_CALENDAR_LOOKUP_PATTERN = re.compile(
    r"\b(?:what do i have|what am i doing|what(?:'s| is) happening|when is|show me my|coming up|what(?:'s| is) on my)\b",
    re.IGNORECASE,
)
PERSONAL_CALENDAR_EXISTENCE_PATTERN = re.compile(
    r"^\s*(?:is|are)\b.*\b(?:on|in)\s+(?:my\s+)?(?:personal\s+)?calendar\b",
    re.IGNORECASE,
)
PERSONAL_CALENDAR_PLAN_PATTERN = re.compile(
    r"(?:\b(?:is coming|are coming|am going|is going|are going|dinner|appointment|game|trivia|plans?)\b"
    r".*\b(?:today|tonight|tomorrow|next|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b|"
    r"\b(?:i\s+)?have\s+(?:trivia|dinner|an?\s+appointment|plans?|[a-z]+\s+with\s+[a-z]+)\b"
    r".*\b(?:today|tonight|tomorrow|next|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b)",
    re.IGNORECASE,
)
PERSONAL_CALENDAR_DATED_COMMITMENT_PATTERN = re.compile(
    r"(?=[\s\S]*\b(?:i(?:'m| am)\s+going|taking\s+pto|[a-z][a-z'-]*\s+is\s+coming|"
    r"appointment|company\s+retreat|alumni\s+weekend|\w+\s+game)\b)"
    r"(?=[\s\S]*\b(?:today|tomorrow|this|next|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"january|february|march|april|may|june|july|august|september|october|november|december)\b)",
    re.IGNORECASE,
)
PERSONAL_PLAN_EXCLUSION_PATTERN = re.compile(
    r"\b(?:might|may|maybe|thinking about|wish|should i|if|went|came|last|yesterday)\b",
    re.IGNORECASE,
)
DRIVE_TERMS = ("drive", "document", "documents", "file", "files", "folder")
ASANA_TERMS = ("asana", "overdue tasks", "what's overdue", "what is overdue", "show me my overdue tasks")
FREE_TIME_PATTERN = re.compile(r"\bfree\b.*\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|morning|afternoon|evening|today|tomorrow)\b")
GMAIL_FOLLOWUP_PATTERN = re.compile(r"\bwhat did\s+[\w .'-]+\s+say\??$")
DRIVE_RELATIONSHIP_PATTERN = re.compile(r"\b(?:which|what|who)\b.*\bclients?\b|\bcompanies\s+are\s+clients\b|\b(?:does|do)\b.*\bserve\b")
ASANA_TASK_PATTERN = re.compile(r"\bwhat\s+(?:tasks?|do i)\b.*\bdue\s+today\b|\bwhat\s+do\s+i\s+have\s+due\s+today\b")
REMINDER_REQUEST_PATTERN = re.compile(
    r"\bremind me\s+(?:to|about|in\s+(?:(?:an?|\d+)\s+)?(?:seconds?|minutes?|hours?)\s+to)\b|\breminder\s*:|\b(?:cancel|complete|finish|mark|move|change|update)\s+(?:my\s+)?(?:reminder|task)\b|"
    r"\b(?:what are my reminders|list my reminders|show my reminders|what do i need to get done)\b",
    re.IGNORECASE,
)
REMINDER_TIME_REPLY_PATTERN = re.compile(r"^\s*(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm|in\s+the\s+morning)?\s*[.!]?\s*$", re.IGNORECASE)
OPERATIONAL_SUMMARY_PATTERN = re.compile(r"\bwhat needs to get done(?: today)?\b", re.IGNORECASE)


def _matches(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def route_request(user_text: str) -> RoutingDecision:
    """Identify the best current or future owner for a request.

    Planned agents are identified with ``delegation_ready=False`` so callers
    can preserve a safe Sheila fallback until those implementations exist.
    """
    normalized = user_text.lower()
    if _matches(normalized, ASANA_TERMS) or ASANA_TASK_PATTERN.search(normalized):
        return RoutingDecision("Sheila", "asana_read", False, "This request needs read-only Asana data.", "asana")
    if OPERATIONAL_SUMMARY_PATTERN.search(normalized):
        return RoutingDecision("Sheila", "operational_summary", False, "This request needs Sheila's operational task and calendar summary.", "operational_summary")
    if REMINDER_REQUEST_PATTERN.search(normalized) and not PERSONAL_PLAN_EXCLUSION_PATTERN.search(normalized):
        return RoutingDecision("Sheila", "sheila_task", False, "This request needs Sheila's reminder and task store.", "sheila_task")
    if REMINDER_TIME_REPLY_PATTERN.match(user_text):
        return RoutingDecision("Sheila", "sheila_task", False, "This may complete Sheila's pending reminder.", "sheila_task")
    non_calendar_mutation_terms = ("file", "document", "task", "email")
    is_non_calendar_mutation = any(term in normalized for term in non_calendar_mutation_terms)
    is_tentative_or_historical = PERSONAL_PLAN_EXCLUSION_PATTERN.search(normalized)
    is_generic_calendar_request = _matches(normalized, CALENDAR_TERMS)
    if (EXISTING_TRAVEL_INFO_PATTERN.search(normalized) or
            ("weekend" in normalized and "month" in normalized and "coming up" in normalized) or
            PERSONAL_CALENDAR_EXISTENCE_PATTERN.search(normalized) or
            _matches(normalized, PERSONAL_CALENDAR_TERMS) or
            ("calendar" in normalized and re.search(r"\b(?:add|put|commit|save|remember)\b", normalized)) or
            (PERSONAL_CALENDAR_LOOKUP_PATTERN.search(normalized) and not is_generic_calendar_request) or
            (not is_tentative_or_historical and not is_generic_calendar_request and
             (PERSONAL_CALENDAR_PLAN_PATTERN.search(normalized) or
              PERSONAL_CALENDAR_DATED_COMMITMENT_PATTERN.search(normalized)) and "?" not in user_text) or
            (not is_tentative_or_historical and PERSONAL_CALENDAR_COMMAND_PATTERN.search(normalized) and
             not is_non_calendar_mutation and not is_generic_calendar_request)):
        return RoutingDecision("Sheila", "personal_calendar", False, "This request needs Sheila's personal calendar.", "personal_calendar")
    # Google data is a Sheila capability, not a separate personality agent.
    if _matches(normalized, GMAIL_TERMS) or GMAIL_FOLLOWUP_PATTERN.search(normalized):
        return RoutingDecision("Sheila", "gmail_read", False, "This request needs read-only Gmail data.", "gmail")
    if _matches(normalized, CALENDAR_TERMS) or FREE_TIME_PATTERN.search(normalized):
        return RoutingDecision("Sheila", "calendar_read", False, "This request needs read-only Google Calendar data.", "calendar")
    if _matches(normalized, DRIVE_TERMS) or DRIVE_RELATIONSHIP_PATTERN.search(normalized):
        return RoutingDecision("Sheila", "drive_read", False, "This request needs read-only Google Drive data.", "drive")
    if _matches(normalized, FINANCIAL_TERMS):
        return RoutingDecision(
            agent="Richard",
            intent="financial_or_tax",
            delegation_ready=False,
            reason="This appears to be a financial or tax request for Richard once available.",
        )
    if _matches(normalized, TRAVEL_TERMS) and TRAVEL_OPERATION_PATTERN.search(normalized):
        return RoutingDecision(
            agent="Travel",
            intent="travel_planning_or_research",
            delegation_ready=False,
            reason="This appears to be a travel request for the future Travel specialist.",
        )
    if _matches(normalized, RESEARCH_TERMS):
        return RoutingDecision(
            agent="Research",
            intent="deep_research_or_analysis",
            delegation_ready=False,
            reason="This appears to need deep research, analysis, or presentation support.",
        )
    return RoutingDecision(
        agent="Sheila",
        intent="general_coordination",
        delegation_ready=False,
        reason="Sheila can handle this request directly.",
    )
