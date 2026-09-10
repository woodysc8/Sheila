import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import extract_historical_memories as extractor
import memory


def response(memories=None, events=None, review=None, skipped=None):
    return json.dumps({"candidate_memories": memories or [], "candidate_events": events or [],
                       "review_only_candidates": review or [], "skipped_or_low_value": skipped or []})


def candidate(exchange_id, excerpt, provenance="USER_STATED", speaker="USER", **changes):
    value = {"category": "preference", "content": "The user prefers Command Prompt.",
             "importance": 7, "confidence": 1, "memory_key": "preference.communication.terminal",
             "exchange_ids": [exchange_id], "provenance": provenance,
             "evidence_sources": [{"exchange_id": exchange_id, "speaker": speaker, "excerpt": excerpt}],
             "conflict": False}
    value.update(changes)
    return value


class HistoricalExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "memory.db")
        self.db_patch = patch.object(extractor.config, "DB_PATH", self.db_path)
        self.db_patch.start()
        memory.config.DB_PATH = self.db_path
        memory.init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def add_exchange(self, user, assistant="Acknowledged.", timestamp="2026-08-01T09:00:00"):
        connection = sqlite3.connect(self.db_path)
        cursor = connection.execute("INSERT INTO exchanges (timestamp, user_text, assistant_text, important) VALUES (?, ?, ?, 0)",
                                    (timestamp, user, assistant))
        connection.commit()
        connection.close()
        return cursor.lastrowid

    def plan(self, payload):
        return extractor.extract_plan(extractor.load_exchanges(), ask=lambda _prompt: payload)

    def test_loading_uses_configured_database_in_chronological_order(self):
        later = self.add_exchange("later", timestamp="2026-08-02T09:00:00")
        earlier = self.add_exchange("earlier", timestamp="2026-08-01T09:00:00")
        self.assertEqual([item["id"] for item in extractor.load_exchanges()], [earlier, later])

    def test_user_stated_fact_is_primary_with_provenance_and_evidence(self):
        exchange_id = self.add_exchange("I prefer Command Prompt.")
        report = self.plan(response(memories=[candidate(exchange_id, "I prefer Command Prompt.")]))
        item = report["candidate_memories"][0]
        self.assertEqual(item["metadata"]["provenance"], "USER_STATED")
        self.assertEqual(item["metadata"]["exchange_ids"], [exchange_id])
        self.assertEqual(item["metadata"]["evidence_sources"][0]["speaker"], "USER")

    def test_assistant_stated_fact_is_review_only(self):
        exchange_id = self.add_exchange("What emails did I get?", "You're Sam Woody and work at Company X.")
        report = self.plan(response(memories=[candidate(exchange_id, "You're Sam Woody and work at Company X.",
            "ASSISTANT_STATED", "ASSISTANT", content="The user works at Company X.")]))
        self.assertEqual(report["candidate_memories"], [])
        self.assertEqual(report["review_only_candidates"][0]["metadata"]["provenance"], "ASSISTANT_STATED")

    def test_inferred_and_system_data_are_review_only(self):
        inferred_id = self.add_exchange("Can you help with my schedule?")
        system_id = self.add_exchange("What is on my calendar?", "Calendar: standup at 9 AM.")
        report = self.plan(response(memories=[
            candidate(inferred_id, "Can you help with my schedule?", "INFERRED", "USER", content="The user has a demanding routine."),
            candidate(system_id, "Calendar: standup at 9 AM.", "SYSTEM_DATA", "ASSISTANT", category="event", content="Standup at 9 AM."),
        ]))
        self.assertEqual(report["candidate_memories"], [])
        self.assertEqual({item["metadata"]["provenance"] for item in report["review_only_candidates"]}, {"INFERRED", "SYSTEM_DATA"})

    def test_user_confirmation_of_assistant_statement_is_primary(self):
        exchange_id = self.add_exchange("Yes.", "You work at Company X, right?")
        item = candidate(exchange_id, "Yes.", content="The user works at Company X.", category="work", memory_key="career.employer")
        item["evidence_sources"].insert(0, {"exchange_id": exchange_id, "speaker": "ASSISTANT", "excerpt": "You work at Company X, right?"})
        report = self.plan(response(memories=[item]))
        self.assertEqual(report["candidate_memories"][0]["metadata"]["provenance"], "USER_STATED")

    def test_unknown_or_unverified_speaker_provenance_is_review_only(self):
        exchange_id = self.add_exchange("I prefer tea.")
        report = self.plan(response(memories=[candidate(exchange_id, "I prefer tea.", "USER_STATED", "UNKNOWN",
            content="The user prefers tea.", memory_key="preference.drink")]))
        item = report["review_only_candidates"][0]
        self.assertEqual(item["metadata"]["provenance"], "INFERRED")
        self.assertIn("could not be verified", item["review_reason"])

    def test_duplicate_work_team_candidates_consolidate(self):
        first = self.add_exchange("My team includes Alex.")
        second = self.add_exchange("Alex is still on my team.")
        shared = {"category": "work", "content": "The user's work team includes Alex.",
                  "importance": 6, "confidence": 1, "memory_key": "work.team_structure"}
        report = self.plan(response(memories=[candidate(first, "My team includes Alex.", **shared),
                                              candidate(second, "Alex is still on my team.", **shared)]))
        self.assertEqual(len(report["candidate_memories"]), 1)
        self.assertEqual(report["candidate_memories"][0]["metadata"]["exchange_ids"], [first, second])

    def test_daily_calendar_email_and_asana_items_are_not_primary(self):
        calendar_id = self.add_exchange("What's on today?", "Calendar: client call at 10.")
        report = self.plan(response(events=[candidate(calendar_id, "Calendar: client call at 10.", "SYSTEM_DATA", "ASSISTANT",
            category="event", content="Client call at 10 AM today.", memory_key=None)]))
        self.assertEqual(report["candidate_events"], [])
        self.assertEqual(len(report["review_only_candidates"]), 1)

    def test_events_remain_separate_from_primary_memories(self):
        exchange_id = self.add_exchange("I traveled to Boston on August 12.")
        report = self.plan(response(events=[candidate(exchange_id, "I traveled to Boston on August 12.", category="event",
            content="The user traveled to Boston on August 12.", memory_key=None)]))
        self.assertEqual(report["candidate_memories"], [])
        self.assertEqual(report["candidate_events"][0]["category"], "event")

    def test_extraction_does_not_modify_database_or_depend_on_sam2(self):
        exchange_id = self.add_exchange("I prefer Command Prompt.")
        before = Path(self.db_path).read_bytes()
        self.plan(response(memories=[candidate(exchange_id, "I prefer Command Prompt.")]))
        self.assertEqual(Path(self.db_path).read_bytes(), before)
        self.assertNotIn("second_brain", extractor.__dict__)

    def test_structured_output_preserves_quotes_apostrophes_and_multiline_text(self):
        exchange_id = self.add_exchange("I prefer Sam's \"night mode\" setup.\nIt helps.")
        api_response = Mock()
        api_response.json.return_value = {"status": "completed", "output_text": response(memories=[candidate(
            exchange_id, "I prefer Sam's \"night mode\" setup.\nIt helps.",
            content="The user prefers Sam's \"night mode\" setup.\nIt helps.", memory_key="preference.technology.night_mode")])}
        with patch.object(extractor.brain.config, "OPENAI_API_KEY", "test-key"), \
             patch.object(extractor.brain.requests, "post", return_value=api_response) as post:
            report = extractor.extract_plan(extractor.load_exchanges())
        self.assertEqual(post.call_args.kwargs["json"]["text"]["format"]["type"], "json_schema")
        self.assertIn("Sam's \"night mode\"", report["candidate_memories"][0]["content"])
        self.assertIn("\n", report["candidate_memories"][0]["content"])

    def test_empty_and_malformed_model_responses_fail_closed(self):
        exchange_id = self.add_exchange("I prefer tea.")
        for payload in ({"status": "completed", "output_text": ""}, {"status": "completed", "output_text": "not json"}):
            api_response = Mock()
            api_response.json.return_value = payload
            with patch.object(extractor.brain.config, "OPENAI_API_KEY", "test-key"), \
                 patch.object(extractor.brain.requests, "post", return_value=api_response):
                report = extractor.extract_plan(extractor.load_exchanges())
            self.assertEqual(report["candidate_memories"], [])
            self.assertEqual(report["extraction_errors"][0]["exchange_ids"], [exchange_id])

    def test_empty_and_invalid_exchange_content_are_safe(self):
        self.add_exchange("")
        self.assertEqual(extractor.load_exchanges(), [])
        report, _ = extractor.generate_report(os.path.join(self.temp_dir.name, "review.json"), ask=lambda _prompt: response())
        self.assertEqual(report["total_exchanges"], 0)


if __name__ == "__main__":
    unittest.main()
