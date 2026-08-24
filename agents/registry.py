"""Definitions for Sheila and the specialists she may delegate to later."""

from dataclasses import dataclass
from typing import Literal


AgentStatus = Literal["active", "planned"]


@dataclass(frozen=True)
class AgentDefinition:
    """A future-facing description of an agent, not an implementation."""

    name: str
    role: str
    status: AgentStatus


AGENTS: dict[str, AgentDefinition] = {
    "Sheila": AgentDefinition(
        name="Sheila",
        role="personal chief of staff / coordinator",
        status="active",
    ),
    "Richard": AgentDefinition(
        name="Richard",
        role="personal financial/tax AI",
        status="planned",
    ),
    "Travel": AgentDefinition(
        name="Travel",
        role="travel planning and travel research AI",
        status="planned",
    ),
    "Research": AgentDefinition(
        name="Research",
        role="deep research, analysis, and presentation AI",
        status="planned",
    ),
}


def get_agent(name: str) -> AgentDefinition:
    """Return an agent definition, raising KeyError for an unknown name."""
    return AGENTS[name]
