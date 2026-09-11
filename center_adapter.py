"""Sheila's narrow direct-Python boundary to Center execution.

Center owns task classification and specialist delegation.  Sheila supplies a
structured execution request and receives Center's outcome as-is; this module
does not read or write memory and has no integration-specific behavior.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
import importlib
from pathlib import Path
import sys
from threading import RLock
from typing import Any

import config


class CenterError(RuntimeError):
    """Base error for Sheila's Center execution boundary."""


class CenterConfigurationError(CenterError):
    """Raised when the configured local Center interface is invalid."""


class CenterUnavailableError(CenterError):
    """Raised when Center cannot accept or execute a submitted task."""


_CENTER_MODULE_NAMES = ("config", "intake", "classifier", "delegation", "specialists")
_IMPORT_LOCK = RLock()


def _validated_request(task: Mapping[str, Any]) -> tuple[str, dict | None, dict | None]:
    if not isinstance(task, Mapping):
        raise ValueError("Center task must be a structured object")

    raw_input = task.get("raw_input")
    if not isinstance(raw_input, str) or not raw_input.strip():
        raise ValueError("Center task requires a non-empty 'raw_input' string")

    context = task.get("context")
    metadata = task.get("metadata")
    if context is not None and not isinstance(context, dict):
        raise ValueError("Center task 'context' must be an object when supplied")
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError("Center task 'metadata' must be an object when supplied")
    return raw_input, context, metadata


@contextmanager
def _center_import_scope(center_path: Path):
    """Load Center's legacy top-level modules without replacing Sheila's ones."""
    saved_modules = {
        name: module for name, module in sys.modules.items()
        if name in _CENTER_MODULE_NAMES or name.startswith("specialists.")
    }
    original_path = list(sys.path)
    try:
        for name in tuple(sys.modules):
            if name in _CENTER_MODULE_NAMES or name.startswith("specialists."):
                del sys.modules[name]
        sys.path.insert(0, str(center_path))
        yield
    finally:
        for name in tuple(sys.modules):
            if name in _CENTER_MODULE_NAMES or name.startswith("specialists."):
                del sys.modules[name]
        sys.modules.update(saved_modules)
        sys.path[:] = original_path


def _load_handler() -> Callable[..., dict]:
    configured_path = config.SHEILA_CENTER_PATH
    if not configured_path:
        raise CenterConfigurationError(
            "Center is not configured. Set SHEILA_CENTER_PATH to the local Center project."
        )
    center_path = Path(configured_path).expanduser()
    if not center_path.is_dir():
        raise CenterConfigurationError(
            f"Center is misconfigured: SHEILA_CENTER_PATH is not a directory: {center_path}"
        )

    module_name, separator, attribute = config.SHEILA_CENTER_HANDLER.rpartition(".")
    if not separator or not module_name or not attribute:
        raise CenterConfigurationError(
            "Center is misconfigured: SHEILA_CENTER_HANDLER must be 'module.callable'."
        )
    try:
        module = importlib.import_module(module_name)
        handler = getattr(module, attribute)
    except (ImportError, AttributeError) as exc:
        raise CenterConfigurationError(
            f"Center interface '{config.SHEILA_CENTER_HANDLER}' could not be loaded."
        ) from exc
    if not callable(handler):
        raise CenterConfigurationError(
            f"Center interface '{config.SHEILA_CENTER_HANDLER}' is not callable."
        )
    return handler


def submit_execution_task(task: Mapping[str, Any]) -> dict:
    """Submit one structured task to Center and return Center's result unchanged."""
    raw_input, context, metadata = _validated_request(task)
    with _IMPORT_LOCK:
        configured_path = config.SHEILA_CENTER_PATH
        if not configured_path:
            _load_handler()  # Raises the standardized configuration error.
        center_path = Path(configured_path).expanduser()
        with _center_import_scope(center_path):
            handler = _load_handler()
            try:
                return handler(raw_input, context=context, metadata=metadata)
            except Exception as exc:
                raise CenterUnavailableError("Center could not execute this task.") from exc
