"""Small authority-aware contracts shared at Sheila's execution boundaries."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


Authority = Literal["google_calendar", "sam2", "center", "gmail", "sheila"]
Status = Literal["succeeded", "failed", "partial"]


@dataclass(frozen=True)
class RequestContext:
    request_id: str
    user_id: str
    user_text: str
    conversation_context: dict[str, Any] = field(default_factory=dict)
    relevant_memory: tuple[dict[str, Any], ...] = ()
    requested_action: str = ""
    target_system: str = ""
    constraints: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecutionRequest:
    request_id: str
    capability: str
    task: str
    relevant_context: dict[str, Any] = field(default_factory=dict)
    constraints: tuple[str, ...] = ()
    authority_scope: str = "read"

    def to_center_task(self) -> dict[str, Any]:
        """Adapt to Center's existing narrow Python interface."""
        return {
            "raw_input": self.task,
            "context": self.relevant_context,
            "metadata": {
                "request_id": self.request_id,
                "capability": self.capability,
                "authority_scope": self.authority_scope,
                "constraints": list(self.constraints),
            },
        }


@dataclass(frozen=True)
class ExecutionResult:
    request_id: str
    status: Status
    result: Any = None
    authoritative_source: Authority = "sheila"
    side_effects: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    durable_memory_candidate: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
