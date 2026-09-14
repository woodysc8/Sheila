"""Thin orchestration helpers; authorities remain in their owning systems."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import uuid4
from typing import Any

import center_adapter
import config
import memory
import personal_calendar
from sam2_memory_client import Sam2MemoryClient, Sam2MemorySearchError
from orchestration_contract import (
    ExecutionRequest,
    ExecutionResult,
    RequestContext,
    SpecialistDelegationRequest,
)


logger = logging.getLogger(__name__)


_CENTER_SPECIALISTS = {
    "Travel": "juan_whey",
    "Richard": "richard",
}

# Juan's Center adapter is intentionally a non-user-facing stub today.  This
# explicit state makes Travel a known execution candidate without activating
# it in Sheila's conversational workflow.
TRAVEL_SPECIALIST_EXECUTION_ENABLED = False


@dataclass(frozen=True)
class SpecialistExecutionDecision:
    """Sheila's explicit readiness decision after deterministic routing."""

    routed_agent: str
    is_specialist_candidate: bool
    may_execute: bool
    reason: str


def specialist_execution_decision(routed_agent: str) -> SpecialistExecutionDecision:
    """Decide whether an already-routed specialist may execute now."""
    if routed_agent == "Travel":
        if TRAVEL_SPECIALIST_EXECUTION_ENABLED:
            return SpecialistExecutionDecision(
                "Travel", True, True, "Travel specialist execution is enabled."
            )
        return SpecialistExecutionDecision(
            "Travel", True, False, "Travel specialist execution is disabled while Juan remains a stub."
        )
    if routed_agent == "Richard":
        return SpecialistExecutionDecision(
            "Richard", True, False, "Richard remains a future specialist."
        )
    if routed_agent == "Sheila":
        return SpecialistExecutionDecision(
            "Sheila", False, False, "This request remains with Sheila."
        )
    return SpecialistExecutionDecision(
        routed_agent, False, False, "No executable specialist is registered for this route."
    )


def request_context(user_text: str, *, action: str, target: str,
                    conversation_context: dict[str, Any] | None = None,
                    relevant_memory: list[dict[str, Any]] | None = None) -> RequestContext:
    context = RequestContext(
        request_id=uuid4().hex,
        user_id=config.SHEILA_USER_ID,
        user_text=user_text,
        conversation_context=conversation_context or {},
        relevant_memory=tuple(relevant_memory or ()),
        requested_action=action,
        target_system=target,
    )
    logger.info("orchestration_request_received request_id=%s action=%s target=%s", context.request_id, action, target)
    return context


def retrieve_relevant_memory(
    *,
    query: str = "",
    category: str | None = None,
    source: str | None = None,
    memory_key: str | None = None,
    sort: str = "relevance",
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Explicitly retrieve a bounded set of durable Sam 2 context records.

    Callers choose when memory is relevant.  This helper neither persists data
    nor attaches results to Center or a specialist request automatically.
    """
    return Sam2MemoryClient().search(
        query,
        category=category,
        source=source,
        memory_key=memory_key,
        sort=sort,
        limit=limit,
    )


def _travel_memory_context(memories: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Select the small preference/fact shape Juan may use for this request."""
    selected: list[dict[str, str]] = []
    for memory_record in memories[:6]:
        content = memory_record.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        item = {"content": content}
        category = memory_record.get("category")
        if isinstance(category, str) and category.strip():
            item["category"] = category
        selected.append(item)
    return selected


def delegate_routed_travel_with_memory(
    context: RequestContext,
    routed_agent: str,
    task: str,
    *,
    constraints: tuple[str, ...] = (),
    authority_scope: str = "read",
) -> ExecutionResult:
    """Explicitly prepare a routed Travel task with bounded Sam 2 context.

    This is intentionally not part of the general conversational workflow
    while Juan remains a stub.  It is the eventual Travel execution boundary
    after Sheila has already selected the Travel specialist.
    """
    if routed_agent != "Travel":
        return ExecutionResult(
            context.request_id,
            "failed",
            authoritative_source="sheila",
            errors=("Travel memory preparation requires a Travel routing decision.",),
        )
    try:
        memories = retrieve_relevant_memory(query=task, category="travel", limit=6)
    except Sam2MemorySearchError:
        logger.warning("travel_memory_retrieval request_id=%s status=unavailable", context.request_id)
        relevant_context: dict[str, Any] = {}
    else:
        relevant_context = {"memory_context": _travel_memory_context(memories)}
    return delegate_routed_specialist_with_center(
        context,
        routed_agent,
        task,
        relevant_context=relevant_context,
        constraints=constraints,
        authority_scope=authority_scope,
    )


def execute_routed_specialist_if_ready(
    context: RequestContext,
    routed_agent: str,
    task: str,
    *,
    constraints: tuple[str, ...] = (),
    authority_scope: str = "read",
) -> ExecutionResult | None:
    """Execute only a Sheila-approved, currently enabled specialist route.

    ``None`` means the caller must retain its normal Sheila fallback.  The
    current Travel state is disabled, so this does not expose Juan's stub.
    """
    decision = specialist_execution_decision(routed_agent)
    if not decision.may_execute:
        return None
    return delegate_routed_travel_with_memory(
        context,
        routed_agent,
        task,
        constraints=constraints,
        authority_scope=authority_scope,
    )


def execute_with_center(context: RequestContext, capability: str, task: str,
                        *, authority_scope: str = "read") -> ExecutionResult:
    """Delegate one bounded execution task to Center without sharing memory access."""
    request = ExecutionRequest(
        request_id=context.request_id,
        capability=capability,
        task=task,
        relevant_context=context.conversation_context,
        constraints=("Center must not persist user memory", "return authoritative source and status"),
        authority_scope=authority_scope,
    )
    logger.info("center_delegation request_id=%s capability=%s", context.request_id, capability)
    try:
        center_task = request.to_center_task()
        # Direct Python is the existing integration boundary.  Center receives
        # only a bounded read callable, never OAuth material or a data store.
        if capability == "calendar_read":
            center_task["metadata"]["calendar_reader"] = personal_calendar.get_personal_calendar_events
        outcome = center_adapter.submit_execution_task(center_task)
    except center_adapter.CenterError as exc:
        logger.warning("center_result request_id=%s status=failed", context.request_id)
        return ExecutionResult(context.request_id, "failed", authoritative_source="center", errors=(str(exc),))
    status = str(outcome.get("status", "failed")).lower() if isinstance(outcome, dict) else "failed"
    if status not in {"succeeded", "done", "success"}:
        return ExecutionResult(context.request_id, "failed", outcome, "center", errors=("Center did not confirm execution.",))
    authoritative_source = outcome.get("authoritative_source", "center") if isinstance(outcome, dict) else "center"
    # Center may coordinate, but cannot declare itself an authority for user data.
    if authoritative_source not in {"google_calendar", "gmail", "center"}:
        authoritative_source = "center"
    logger.info("center_result request_id=%s status=succeeded source=%s", context.request_id, authoritative_source)
    return ExecutionResult(context.request_id, "succeeded", outcome, authoritative_source)


def delegate_routed_specialist_with_center(
    context: RequestContext,
    routed_agent: str,
    task: str,
    *,
    relevant_context: dict[str, Any] | None = None,
    constraints: tuple[str, ...] = (),
    authority_scope: str = "read",
) -> ExecutionResult:
    """Hand an already-selected specialist request to Center's fixed boundary.

    Sheila retains the routing decision.  Only the context explicitly supplied
    to this call crosses the boundary; request conversation state and Sam 2
    recall stay in Sheila.
    """
    specialist = _CENTER_SPECIALISTS.get(routed_agent)
    if specialist is None:
        return ExecutionResult(
            context.request_id,
            "failed",
            authoritative_source="center",
            errors=(f"No structured Center specialist is registered for {routed_agent}.",),
        )
    request = SpecialistDelegationRequest(
        request_id=context.request_id,
        specialist=specialist,
        task=task,
        relevant_context=dict(relevant_context or {}),
        constraints=constraints,
        authority_scope=authority_scope,
    )
    logger.info("center_specialist_delegation request_id=%s specialist=%s", context.request_id, specialist)
    try:
        outcome = center_adapter.submit_execution_task(request.to_center_task())
    except center_adapter.CenterError as exc:
        logger.warning("center_specialist_result request_id=%s status=failed", context.request_id)
        return ExecutionResult(context.request_id, "failed", authoritative_source="center", errors=(str(exc),))
    status = str(outcome.get("status", "failed")).lower() if isinstance(outcome, dict) else "failed"
    if status not in {"succeeded", "done", "success"}:
        return ExecutionResult(context.request_id, "failed", outcome, "center", errors=("Center did not confirm specialist delegation.",))
    logger.info("center_specialist_result request_id=%s status=succeeded specialist=%s", context.request_id, specialist)
    return ExecutionResult(context.request_id, "succeeded", outcome, "center")


def persist_memory_candidate(context: RequestContext, candidate: dict[str, Any]) -> ExecutionResult:
    """Persist a compact durable fact through Sam 2's configured provider."""
    logger.info("memory_candidate request_id=%s category=%s", context.request_id, candidate.get("category", "personal"))
    try:
        stored = memory.remember(
            str(candidate.get("category", "personal")),
            str(candidate["content"]),
            "user",
            importance=int(candidate.get("importance", 4)),
            metadata=dict(candidate.get("metadata", {})),
        )
    except Exception as exc:
        logger.warning("memory_write_result request_id=%s status=failed", context.request_id)
        return ExecutionResult(context.request_id, "failed", authoritative_source="sam2", errors=(str(exc),))
    logger.info("memory_write_result request_id=%s status=succeeded", context.request_id)
    return ExecutionResult(context.request_id, "succeeded", stored, "sam2")
