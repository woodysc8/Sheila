"""Thin orchestration helpers; authorities remain in their owning systems."""

from __future__ import annotations

import logging
from uuid import uuid4
from typing import Any

import center_adapter
import config
import memory
import personal_calendar
from orchestration_contract import ExecutionRequest, ExecutionResult, RequestContext


logger = logging.getLogger(__name__)


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
