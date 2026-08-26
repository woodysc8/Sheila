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
RESEARCH_TERMS = (
    "research", "deep research", "research report", "analyze",
    "analysis", "compare", "presentation", "briefing deck", "market study",
)
GMAIL_TERMS = ("email", "emails", "mail", "inbox", "gmail")
CALENDAR_TERMS = ("calendar", "meeting", "meetings", "agenda", "when am i free", "when i'm free", "am i free")
DRIVE_TERMS = ("drive", "document", "documents", "file", "files", "folder")
ASANA_TERMS = ("asana", "overdue tasks", "what's overdue", "what is overdue", "show me my overdue tasks")
FREE_TIME_PATTERN = re.compile(r"\bfree\b.*\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|morning|afternoon|evening|today|tomorrow)\b")
GMAIL_FOLLOWUP_PATTERN = re.compile(r"\bwhat did\s+[\w .'-]+\s+say\??$")
DRIVE_RELATIONSHIP_PATTERN = re.compile(r"\b(?:which|what|who)\b.*\bclients?\b|\bcompanies\s+are\s+clients\b|\b(?:does|do)\b.*\bserve\b")
ASANA_TASK_PATTERN = re.compile(r"\bwhat\s+(?:tasks?|do i)\b.*\bdue\s+today\b|\bwhat\s+do\s+i\s+have\s+due\s+today\b")


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
    if _matches(normalized, TRAVEL_TERMS):
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
