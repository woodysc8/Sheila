"""Local intent routing for Sheila's present and future specialists.

Routing is intentionally deterministic for now. It does not create another
model client or attempt to call planned agents.
"""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class RoutingDecision:
    agent: str
    intent: str
    delegation_ready: bool
    reason: str

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


def _matches(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def route_request(user_text: str) -> RoutingDecision:
    """Identify the best current or future owner for a request.

    Planned agents are identified with ``delegation_ready=False`` so callers
    can preserve a safe Sheila fallback until those implementations exist.
    """
    normalized = user_text.lower()
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
