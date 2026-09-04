import os
import tempfile
import unittest
from unittest.mock import patch

import memory


class StructuredMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "memory.db")
        self.db_patch = patch.object(memory.config, "DB_PATH", self.db_path)
        self.db_patch.start()
        memory.init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_create_retrieve_update_forget_and_provenance(self):
        item = memory.remember(
            "preference",
            "Sam prefers Boston for flights.",
            "telegram",
            source_id="message-42",
            importance=5,
            metadata={"confidence": "explicit"},
        )

        self.assertEqual(item["category"], "preference")
        self.assertEqual(item["source"], "telegram")
        self.assertEqual(item["source_id"], "message-42")
        self.assertEqual(item["metadata"], {"confidence": "explicit"})
        self.assertEqual(memory.recall("Boston"), [item])

        updated = memory.update(item["id"], content="Sam prefers Providence for flights.", importance=3)
        self.assertEqual(updated["content"], "Sam prefers Providence for flights.")
        self.assertEqual(updated["created_at"], item["created_at"])
        self.assertEqual(memory.recall("Boston"), [])
        self.assertEqual(memory.recall("Providence")[0]["source"], "telegram")

        self.assertTrue(memory.forget(item["id"]))
        self.assertEqual(memory.recall(), [])
        self.assertFalse(memory.forget(item["id"]))

    def test_structured_memory_survives_reopening_storage_layer(self):
        memory.remember("project", "AI Network is active.", "manual")
        memory.init_db()
        self.assertEqual(memory.recall("AI Network")[0]["content"], "AI Network is active.")

    def test_existing_conversation_memory_behavior_remains(self):
        memory.log_exchange("Hello", "Hello there", important=True)
        context = memory.get_context(current_query="Hello")
        self.assertIn("User: Hello", context)
        self.assertIn("Iris: Hello there", context)
        self.assertEqual(memory.get_structured_context(), "No structured memories yet.")


if __name__ == "__main__":
    unittest.main()
