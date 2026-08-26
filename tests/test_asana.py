import unittest
from datetime import date
from unittest.mock import Mock, patch

from integrations import asana


def response(status_code: int, data: dict) -> Mock:
    result = Mock(status_code=status_code)
    result.json.return_value = data
    return result


class AsanaIntegrationTests(unittest.TestCase):
    def test_missing_pat_fails_clearly(self):
        with patch.object(asana.config, "ASANA_PAT", ""):
            with self.assertRaisesRegex(asana.AsanaError, "not configured"):
                asana.get_tasks()

    def test_authentication_failure_is_distinct(self):
        with patch.object(asana.config, "ASANA_PAT", "token"), \
             patch("integrations.asana.requests.get", return_value=response(401, {})):
            with self.assertRaisesRegex(asana.AsanaError, "authentication failed"):
                asana.get_tasks()

    def test_task_retrieval_normalizes_structured_records(self):
        task = {"gid": "task-1", "name": "File report", "completed": False, "due_on": "2026-08-24", "due_at": None,
                "assignee": {"name": "Sam"}, "projects": [{"name": "Operations"}], "workspace": {"name": "StreetCred"}, "permalink_url": "https://app.asana.com/0/1/2"}

        def get(url, **_kwargs):
            if url.endswith("/users/me"):
                return response(200, {"data": {"gid": "me", "name": "Sam"}})
            if url.endswith("/workspaces"):
                return response(200, {"data": [{"gid": "workspace-1", "name": "StreetCred"}]})
            return response(200, {"data": [task]})

        with patch.object(asana.config, "ASANA_PAT", "token"), patch("integrations.asana.requests.get", side_effect=get) as request:
            tasks = asana.get_tasks()
        self.assertEqual(tasks, [{"id": "task-1", "name": "File report", "completed": False, "due_on": "2026-08-24", "due_at": None,
                                  "assignee": "Sam", "project": "Operations", "workspace": "StreetCred", "permalink_url": "https://app.asana.com/0/1/2"}])
        self.assertTrue(any(call.args[0].endswith("/users/me") for call in request.call_args_list))
        self.assertTrue(any(call.args[0].endswith("/workspaces") for call in request.call_args_list))
        self.assertTrue(any(call.args[0].endswith("/tasks") for call in request.call_args_list))

    def test_overdue_status_uses_due_date_and_completion(self):
        today = date(2026, 8, 26)
        self.assertTrue(asana.is_overdue({"completed": False, "due_on": "2026-08-25"}, today))
        self.assertFalse(asana.is_overdue({"completed": True, "due_on": "2026-08-25"}, today))
        self.assertFalse(asana.is_overdue({"completed": False, "due_on": None, "due_at": None}, today))

    def test_overdue_tasks_exclude_completed_and_no_due_date(self):
        tasks = [
            {"id": "late", "completed": False, "due_on": "2026-08-24", "due_at": None},
            {"id": "done", "completed": True, "due_on": "2026-08-24", "due_at": None},
            {"id": "undated", "completed": False, "due_on": None, "due_at": None},
        ]
        with patch("integrations.asana.get_tasks", return_value=tasks):
            result = asana.get_overdue_tasks(current_date=date(2026, 8, 26))
        self.assertEqual([task["id"] for task in result], ["late"])


if __name__ == "__main__":
    unittest.main()
