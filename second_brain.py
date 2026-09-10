"""Small HTTP adapter for Sheila's Sam 2 personal-memory API."""

import requests

import config


class SecondBrainError(RuntimeError):
    """Raised when Sam 2 cannot complete a memory request."""


class SecondBrainClient:
    """Call Sam 2 without exposing HTTP details to Sheila's callers."""

    def __init__(self, base_url: str = None, token: str = None, timeout: float = None):
        self.base_url = (base_url or config.SECOND_BRAIN_URL).rstrip("/")
        self.token = token if token is not None else config.SECOND_BRAIN_SERVICE_TOKEN
        self.user_id = config.SECOND_BRAIN_USER_ID
        self.timeout = timeout if timeout is not None else config.SECOND_BRAIN_TIMEOUT

    def _request(self, method: str, path: str, **kwargs) -> dict:
        if not self.base_url or not self.token:
            raise SecondBrainError("Sam 2 memory configuration is incomplete")
        headers = {
            "Authorization": f"Bearer {self.token}",
            "X-Second-Brain-User": self.user_id,
        }
        headers.update(kwargs.pop("headers", {}))
        try:
            response = requests.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                timeout=self.timeout,
                **kwargs,
            )
            response.raise_for_status()
            return response.json() if response.content else {}
        except (requests.RequestException, ValueError) as exc:
            raise SecondBrainError(str(exc)) from exc

    def remember(self, category: str, content: str, source: str, source_id: str = None,
                 importance: int = 0, metadata: dict = None) -> dict:
        """Create or replace one keyed durable memory in Sam 2."""
        payload = {
            "category": category,
            "content": content,
            "source": source,
            "source_id": source_id,
            "importance": importance,
            "memory_key": (metadata or {}).get("memory_key"),
            "metadata": metadata or {},
        }
        return self._request("POST", "/api/memories", json=payload)

    def recall(self, query: str = "", category: str = None, limit: int = 10,
               source: str = None, sort: str = "relevance") -> list[dict]:
        """Search Sam 2 memories using its lexical memory endpoint."""
        payload = {
            "query": query,
            "category": category,
            "source": source,
            "sort": sort,
            "limit": limit,
        }
        result = self._request("POST", "/api/memories/search", json=payload)
        return result.get("memories", [])

    def update(self, memory_id: str, **changes) -> dict:
        """Update one Sam 2 memory by id."""
        return self._request("PATCH", f"/api/memories/{memory_id}", json=changes)

    def forget(self, memory_id: str) -> bool:
        """Delete one Sam 2 memory by id."""
        self._request("DELETE", f"/api/memories/{memory_id}")
        return True