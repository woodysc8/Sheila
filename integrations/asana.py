"""Bounded, read-only Asana REST API helpers."""

from datetime import date, datetime

import requests

import config


ASANA_API_URL = "https://app.asana.com/api/1.0"
TASK_FIELDS = "gid,name,completed,due_on,due_at,assignee.name,projects.name,workspace.name,permalink_url"


class AsanaError(RuntimeError):
    """Raised when read-only Asana data cannot be retrieved."""


def _token() -> str:
    token = config.ASANA_PAT.strip()
    if not token or "PUT_YOUR" in token.upper():
        raise AsanaError("Asana is not configured. Add ASANA_PAT to the environment.")
    return token


def _get(path: str, params: dict[str, object] | None = None) -> dict:
    try:
        response = requests.get(
            f"{ASANA_API_URL}{path}",
            headers={"Authorization": f"Bearer {_token()}"},
            params=params,
            timeout=20,
        )
        if response.status_code in {401, 403}:
            raise AsanaError("Asana authentication failed. Check ASANA_PAT.")
        response.raise_for_status()
        return response.json()
    except AsanaError:
        raise
    except requests.RequestException as exc:
        raise AsanaError("Asana is unavailable right now.") from exc
    except (TypeError, ValueError) as exc:
        raise AsanaError("Asana returned an invalid response.") from exc


def get_authenticated_user() -> dict[str, str]:
    user = _get("/users/me", {"opt_fields": "gid,name,email"}).get("data", {})
    return {"id": user.get("gid", ""), "name": user.get("name", ""), "email": user.get("email", "")}


def list_workspaces() -> list[dict[str, str]]:
    # Resolve the authenticated user first; no workspace ID is assumed.
    get_authenticated_user()
    return [
        {"id": item.get("gid", ""), "name": item.get("name", "")}
        for item in _get("/workspaces", {"opt_fields": "gid,name"}).get("data", [])
    ]


def list_projects(workspace_id: str, limit: int = 20) -> list[dict[str, str]]:
    data = _get("/projects", {"workspace": workspace_id, "archived": "false", "limit": min(max(limit, 1), 20), "opt_fields": "gid,name,workspace.name"}).get("data", [])
    return [{"id": item.get("gid", ""), "name": item.get("name", ""), "workspace": item.get("workspace", {}).get("name", "")} for item in data]


def normalize_task(item: dict, workspace_name: str = "") -> dict[str, object]:
    projects = item.get("projects") or []
    project_names = [project.get("name", "") for project in projects if project.get("name")]
    return {
        "id": item.get("gid", item.get("id", "")),
        "name": item.get("name", ""),
        "completed": bool(item.get("completed", False)),
        "due_on": item.get("due_on"),
        "due_at": item.get("due_at"),
        "assignee": (item.get("assignee") or {}).get("name", ""),
        "project": ", ".join(project_names),
        "workspace": (item.get("workspace") or {}).get("name", workspace_name),
        "permalink_url": item.get("permalink_url", ""),
    }


def get_tasks(limit: int = 20) -> list[dict[str, object]]:
    """Return up to ``limit`` incomplete assigned tasks across accessible workspaces."""
    tasks: list[dict[str, object]] = []
    seen: set[str] = set()
    for workspace in list_workspaces():
        data = _get("/tasks", {
            "assignee": "me", "workspace": workspace["id"], "completed_since": "now",
            "limit": min(max(limit, 1), 20), "opt_fields": TASK_FIELDS,
        }).get("data", [])
        for item in data:
            task = normalize_task(item, workspace["name"])
            if task["id"] and task["id"] not in seen:
                seen.add(str(task["id"]))
                tasks.append(task)
            if len(tasks) >= min(max(limit, 1), 20):
                return tasks
    return tasks


def is_overdue(task: dict[str, object], current_date: date | None = None) -> bool:
    """Overdue means incomplete and due strictly before the local current date."""
    if task.get("completed"):
        return False
    today = current_date or datetime.now().astimezone().date()
    due_on = task.get("due_on")
    if isinstance(due_on, str) and due_on:
        try:
            return date.fromisoformat(due_on) < today
        except ValueError:
            return False
    due_at = task.get("due_at")
    if isinstance(due_at, str) and due_at:
        try:
            return datetime.fromisoformat(due_at.replace("Z", "+00:00")).astimezone().date() < today
        except ValueError:
            return False
    return False


def get_overdue_tasks(limit: int = 20, current_date: date | None = None) -> list[dict[str, object]]:
    return [task for task in get_tasks(limit=limit) if is_overdue(task, current_date)][:min(max(limit, 1), 20)]
