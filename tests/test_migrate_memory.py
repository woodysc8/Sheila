import json
import os
import tempfile
import unittest
from unittest.mock import patch

import memory
import migrate_memory


class FakeSecondBrainClient:
    def __init__(self, existing=None):
        self.existing = dict(existing or {})
        self.recall_calls = []
        self.remember_calls = []

    def recall(self, **kwargs):
        self.recall_calls.append(kwargs)
        item = self.existing.get(kwargs["memory_key"])
        return [item] if item else []

    def remember(self, category, content, source, source_id=None, importance=0, metadata=None):
        self.remember_calls.append({
            "category": category,
            "content": content,
            "source": source,
            "source_id": source_id,
            "importance": importance,
            "metadata": metadata,
        })
        memory_key = (metadata or {}).get("memory_key")
        response = {"id": f"remote-{len(self.remember_calls)}"}
        if memory_key:
            self.existing[memory_key] = response
        return response


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "memory.db")
        self.db_patch = patch.object(memory.config, "DB_PATH", self.db_path)
        self.db_patch.start()
        memory.init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _insert(self, **values):
        defaults = {
            "category": "preference",
            "content": "Sam prefers Hyatt hotels.",
            "source": "user",
            "source_id": "source-1",
            "importance": 5,
            "metadata": json.dumps({"explicit": True, "memory_key": "hotel"}),
        }
        defaults.update(values)
        conn = memory._connect()
        cursor = conn.execute(
            """INSERT INTO structured_memories
               (category, content, source, source_id, importance, created_at, updated_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                defaults["category"],
                defaults["content"],
                defaults["source"],
                defaults["source_id"],
                defaults["importance"],
                "2026-09-10T10:00:00",
                "2026-09-10T10:00:00",
                defaults["metadata"],
            ),
        )
        conn.commit()
        row_id = cursor.lastrowid
        conn.close()
        return row_id

    def test_local_record_maps_to_sam2_payload(self):
        record = {
            "id": 7,
            "category": "preference",
            "content": "Sam prefers Hyatt hotels.",
            "source": "user",
            "source_id": "source-1",
            "importance": 5,
            "metadata": {"explicit": True, "memory_key": "hotel"},
        }
        self.assertEqual(
            migrate_memory.local_memory_payload(record),
            {
                "category": "preference",
                "content": "Sam prefers Hyatt hotels.",
                "source": "user",
                "source_id": "source-1",
                "importance": 5,
                "memory_key": "hotel",
                "metadata": {"explicit": True, "memory_key": "hotel"},
            },
        )

    def test_dry_run_performs_zero_remote_writes_and_preserves_local_row(self):
        local_id = self._insert()
        before = memory._connect().execute(
            "SELECT * FROM structured_memories WHERE id = ?", (local_id,)
        ).fetchone()
        client = FakeSecondBrainClient()

        report = migrate_memory.migrate(dry_run=True, client=client)

        after = memory._connect().execute(
            "SELECT * FROM structured_memories WHERE id = ?", (local_id,)
        ).fetchone()
        self.assertEqual(report["total_local_memories"], 1)
        self.assertEqual(report["would_create"][0]["action"], "create")
        self.assertEqual(client.remember_calls, [])
        self.assertEqual(tuple(before), tuple(after))

    def test_duplicate_key_is_reported_and_later_record_is_skipped(self):
        first = self._insert()
        second = self._insert(content="Sam prefers Marriott hotels.")

        report = migrate_memory.build_plan(
            migrate_memory.load_local_memories(),
            client=FakeSecondBrainClient(),
        )

        self.assertEqual(report["valid_memories"], 2)
        self.assertEqual(report["duplicate_key_collisions"][0]["kept_local_id"], first)
        self.assertEqual(report["duplicate_key_collisions"][0]["local_id"], second)
        self.assertEqual(report["would_create"][0]["local_id"], first)
        self.assertEqual(report["skipped"][0]["reason"], "duplicate_local_memory_key")

    def test_invalid_record_is_reported_and_skipped(self):
        records = [{
            "id": 9,
            "category": "preference",
            "content": "Invalid importance",
            "source": "user",
            "source_id": None,
            "importance": 101,
            "metadata": {},
        }]

        report = migrate_memory.build_plan(records, client=FakeSecondBrainClient())

        self.assertEqual(report["valid_memories"], 0)
        self.assertEqual(report["invalid_memories"][0]["local_id"], 9)
        self.assertEqual(report["skipped"][0]["reason"], "invalid_record")
        self.assertEqual(report["remote_operations"], [])

    def test_keyed_migration_is_repeatable_as_replace(self):
        self._insert()
        client = FakeSecondBrainClient()
        records = migrate_memory.load_local_memories()

        first_plan = migrate_memory.build_plan(records, client=client)
        migrate_memory.apply_plan(first_plan, client)
        second_plan = migrate_memory.build_plan(records, client=client)

        self.assertEqual(first_plan["would_create"][0]["action"], "create")
        self.assertEqual(second_plan["would_replace"][0]["action"], "replace")
        self.assertEqual(len(client.remember_calls), 1)


if __name__ == "__main__":
    unittest.main()
