"""Narrow authenticated client for Sheila's Sam 2 memory-search boundary."""

from __future__ import annotations

from typing import Any

import requests

import config


class Sam2MemorySearchError(RuntimeError):
    """Raised when Sheila cannot retrieve an authoritative Sam 2 search result."""


class Sam2MemoryConfigurationError(Sam2MemorySearchError):
    """Raised when the service-to-service Sam 2 settings are incomplete."""


class Sam2MemoryAuthenticationError(Sam2MemorySearchError):
    """Raised when Sam 2 rejects the configured service identity."""


class Sam2MemoryServerError(Sam2MemorySearchError):
    """Raised when Sam 2 reports an unavailable or failed service."""


class Sam2MemoryClient:
    """Search Sam 2 with Sheila's configured service identity only."""

    def __init__(
        self,
        base_url: str | None = None,
        service_token: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self._base_url = (base_url if base_url is not None else config.SECOND_BRAIN_URL).rstrip("/")
        self._service_token = service_token if service_token is not None else config.SECOND_BRAIN_SERVICE_TOKEN
        self._timeout = timeout if timeout is not None else config.SECOND_BRAIN_TIMEOUT

    def _headers(self) -> dict[str, str]:
        user_id = config.SECOND_BRAIN_USER_ID
        if not self._base_url or not self._service_token or not user_id:
            raise Sam2MemoryConfigurationError(
                "Sam 2 memory search requires SECOND_BRAIN_URL, "
                "SECOND_BRAIN_SERVICE_TOKEN, and SECOND_BRAIN_USER_ID."
            )
        return {
            "Authorization": f"Bearer {self._service_token}",
            "X-Second-Brain-User": user_id,
        }

    def search(
        self,
        query: str = "",
        *,
        category: str | None = None,
        source: str | None = None,
        memory_key: str | None = None,
        sort: str = "relevance",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Return normalized Sam 2 memory records; an empty list is successful."""
        if not isinstance(query, str):
            raise ValueError("Sam 2 memory search query must be a string.")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("Sam 2 memory search limit must be a positive integer.")
        payload = {"query": query, "sort": sort, "limit": limit}
        for name, value in {
            "category": category,
            "source": source,
            "memory_key": memory_key,
        }.items():
            if value is not None:
                payload[name] = value
        try:
            response = requests.post(
                f"{self._base_url}/api/memories/search",
                headers=self._headers(),
                json=payload,
                timeout=self._timeout,
            )
            response.raise_for_status()
            body = response.json() if response.content else {}
        except requests.HTTPError as exc:
            status_code = getattr(exc.response, "status_code", None)
            if status_code in {401, 403}:
                raise Sam2MemoryAuthenticationError("Sam 2 rejected Sheila's service identity.") from exc
            if isinstance(status_code, int) and status_code >= 500:
                raise Sam2MemoryServerError("Sam 2 memory search is unavailable.") from exc
            raise Sam2MemorySearchError("Sam 2 memory search request failed.") from exc
        except requests.RequestException as exc:
            raise Sam2MemorySearchError("Sam 2 memory search could not be reached.") from exc
        except ValueError as exc:
            raise Sam2MemorySearchError("Sam 2 memory search returned an invalid response.") from exc

        memories = body.get("memories", []) if isinstance(body, dict) else None
        if not isinstance(memories, list) or not all(isinstance(item, dict) for item in memories):
            raise Sam2MemorySearchError("Sam 2 memory search returned an invalid memories payload.")
        return [dict(item) for item in memories]
