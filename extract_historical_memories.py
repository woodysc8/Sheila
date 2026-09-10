"""Create a read-only review plan from Sheila's historical exchanges.

This utility deliberately has no apply mode and no Sam 2 client dependency.
It opens Sheila's configured SQLite file in SQLite read-only mode and writes
only a JSON review artifact outside the database.
"""

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Callable

import brain
import config


SOURCE_NAME = "sheila_historical_extraction"
DEFAULT_REPORT_NAME = "historical_memory_candidates.json"
BATCH_SIZE = 20

_STRING_OR_NULL = {"anyOf": [{"type": "string"}, {"type": "null"}]}
PROVENANCE_VALUES = ("USER_STATED", "ASSISTANT_STATED", "INFERRED", "SYSTEM_DATA")
_CANDIDATE_PROPERTIES = {
    "category": {"type": "string"}, "content": {"type": "string"},
    "importance": {"type": "integer", "minimum": 1, "maximum": 10},
    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    "memory_key": _STRING_OR_NULL,
    "exchange_ids": {"type": "array", "items": {"type": "integer"}},
    "provenance": {"type": "string", "enum": list(PROVENANCE_VALUES)},
    "evidence_sources": {"type": "array", "items": {"type": "object", "additionalProperties": False,
        "required": ["exchange_id", "speaker", "excerpt"], "properties": {
            "exchange_id": {"type": "integer"},
            "speaker": {"type": "string", "enum": ["USER", "ASSISTANT", "SYSTEM", "UNKNOWN"]},
            "excerpt": {"type": "string"},
        }}},
    "conflict": {"type": "boolean"},
}
EXTRACTION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["candidate_memories", "candidate_events", "review_only_candidates", "skipped_or_low_value"],
    "properties": {
        "candidate_memories": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": list(_CANDIDATE_PROPERTIES), "properties": _CANDIDATE_PROPERTIES}},
        "candidate_events": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": list(_CANDIDATE_PROPERTIES), "properties": _CANDIDATE_PROPERTIES}},
        "review_only_candidates": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": list(_CANDIDATE_PROPERTIES), "properties": _CANDIDATE_PROPERTIES}},
        "skipped_or_low_value": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["exchange_ids", "reason"], "properties": {"exchange_ids": {"type": "array", "items": {"type": "integer"}}, "reason": {"type": "string"}}}},
    },
}


def _read_only_uri(path: str) -> str:
    """Return a SQLite URI which cannot create or modify the target file."""
    return Path(path).resolve().as_uri() + "?mode=ro"


def load_exchanges(db_path: str = None) -> list[dict]:
    """Read usable exchange rows chronologically without initialising SQLite."""
    source = db_path or config.DB_PATH
    connection = sqlite3.connect(_read_only_uri(source), uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT id, timestamp, user_text, assistant_text FROM exchanges ORDER BY timestamp ASC, id ASC"
        ).fetchall()
    finally:
        connection.close()

    exchanges = []
    for row in rows:
        user_text = row["user_text"]
        assistant_text = row["assistant_text"]
        if not isinstance(user_text, str) or not user_text.strip():
            continue
        exchanges.append({
            "id": row["id"],
            "timestamp": row["timestamp"] if isinstance(row["timestamp"], str) else "",
            "user_text": user_text.strip(),
            "assistant_text": assistant_text.strip() if isinstance(assistant_text, str) else "",
        })
    return exchanges


def _excerpt(value: str, limit: int = 240) -> str:
    compact = " ".join(value.split())
    return compact[:limit - 1] + "…" if len(compact) > limit else compact


def build_extraction_prompt(exchanges: list[dict]) -> str:
    """Build a bounded, provenance-first request for Sheila's OpenAI helper."""
    transcript = "\n".join(
        "Exchange {id} ({timestamp})\nUSER: {user}\nASSISTANT: {assistant}".format(
            id=item["id"], timestamp=item["timestamp"],
            user=item["user_text"], assistant=item["assistant_text"],
        )
        for item in exchanges
    )
    return """Review the following Sheila conversation exchanges and return JSON only.

Classify each candidate's provenance as exactly USER_STATED, ASSISTANT_STATED,
INFERRED, or SYSTEM_DATA. USER_STATED requires a direct USER statement or a USER
confirmation of the cited ASSISTANT statement. Never treat an ASSISTANT statement
as user-confirmed merely because the user later changes topic. The row format has
separate USER and ASSISTANT fields; cite each source with its actual speaker and
a short verbatim excerpt. If uncertain, use INFERRED.

Only place genuinely durable, USER_STATED facts in candidate_memories: stable
preferences, identity, relationships, education/career context, long-term goals,
ongoing projects, recurring routines, or persistent constraints. Place all
ASSISTANT_STATED, INFERRED, and SYSTEM_DATA items in review_only_candidates.
Daily email/calendar/Asana information, one-day activity, reporter lists, task
status, greetings, questions, assistant capability explanations, and temporary
requests are not durable memories. A user-stated time-specific life event belongs
in candidate_events; system-derived events belong in review_only_candidates.
Never invent facts, credentials, or secrets. Deduplicate repeated facts. Use one
stable work.team_structure key for semantically overlapping work-team candidates;
flag genuine contradictions rather than resolving them.

Return this JSON object:
{
  "candidate_memories": [{"category":"...","content":"...","importance":1,
    "confidence":0.0,"memory_key":"optional stable key or null",
    "exchange_ids":[1],"provenance":"USER_STATED",
    "evidence_sources":[{"exchange_id":1,"speaker":"USER","excerpt":"short quote"}],
    "conflict":false}],
  "candidate_events": [{"category":"event","content":"...","importance":1,
    "confidence":0.0,"memory_key":null,"exchange_ids":[1],"provenance":"USER_STATED",
    "evidence_sources":[{"exchange_id":1,"speaker":"USER","excerpt":"short quote"}],"conflict":false}],
  "review_only_candidates": [{"category":"...","content":"...","importance":1,
    "confidence":0.0,"memory_key":null,"exchange_ids":[1],"provenance":"ASSISTANT_STATED",
    "evidence_sources":[{"exchange_id":1,"speaker":"ASSISTANT","excerpt":"short quote"}],"conflict":false}],
  "skipped_or_low_value": [{"exchange_ids":[1],"reason":"temporary request"}]
}

EXCHANGES:
""" + transcript


def _json_object(response: str | dict) -> dict:
    """Parse an LLM response, accepting a fenced JSON object when necessary."""
    if isinstance(response, dict):
        return response
    if not isinstance(response, str):
        raise ValueError("extraction response must be a JSON object")
    text = response.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0].strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("extraction response must be a JSON object")
    return value


def _ask_historical_extraction(prompt: str) -> dict:
    """Use Sheila's existing OpenAI boundary with extractor-only strict output."""
    return brain._ask_openai_structured(
        prompt, EXTRACTION_SCHEMA, "historical_memory_plan", max_output_tokens=4000,
    )


def _valid_ids(values, allowed_ids: set[int]) -> list[int]:
    if not isinstance(values, list):
        return []
    return sorted({value for value in values if isinstance(value, int) and value in allowed_ids})


def _evidence_sources(values, exchanges_by_id: dict[int, dict]) -> list[dict]:
    """Keep only evidence whose declared speaker matches the stored row field."""
    if not isinstance(values, list):
        return []
    sources = []
    for value in values:
        if not isinstance(value, dict):
            continue
        exchange_id, speaker, excerpt = value.get("exchange_id"), value.get("speaker"), value.get("excerpt")
        exchange = exchanges_by_id.get(exchange_id)
        if speaker not in {"USER", "ASSISTANT", "SYSTEM", "UNKNOWN"} or not exchange or not isinstance(excerpt, str):
            continue
        excerpt = _excerpt(excerpt)
        actual_text = exchange["user_text"] if speaker == "USER" else exchange["assistant_text"] if speaker == "ASSISTANT" else ""
        verified = bool(excerpt and actual_text and " ".join(excerpt.lower().split()) in " ".join(actual_text.lower().split()))
        sources.append({"exchange_id": exchange_id, "speaker": speaker, "excerpt": excerpt, "verified": verified})
    return sources


def _candidate(item: dict, exchanges_by_id: dict[int, dict], event: bool = False) -> dict | None:
    if not isinstance(item, dict):
        return None
    content = item.get("content")
    exchange_ids = _valid_ids(item.get("exchange_ids"), set(exchanges_by_id))
    if not isinstance(content, str) or not content.strip() or not exchange_ids:
        return None
    category = "event" if event else item.get("category")
    if not isinstance(category, str) or not category.strip():
        return None
    importance = item.get("importance", 1)
    confidence = item.get("confidence", 0.5)
    if isinstance(importance, bool) or not isinstance(importance, int):
        importance = 1
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        confidence = 0.5
    provenance = item.get("provenance", "INFERRED")
    if provenance not in PROVENANCE_VALUES:
        provenance = "INFERRED"
    evidence_sources = _evidence_sources(item.get("evidence_sources"), exchanges_by_id)
    user_evidence = any(source["speaker"] == "USER" and source["verified"] for source in evidence_sources)
    provenance_verified = provenance != "USER_STATED" or user_evidence
    if not provenance_verified:
        provenance = "INFERRED"
    memory_key = item.get("memory_key")
    if not isinstance(memory_key, str) or not memory_key.strip():
        memory_key = None
    return {
        "category": category.strip(),
        "content": content.strip(),
        "importance": max(1, min(10, importance)),
        "confidence": max(0.0, min(1.0, float(confidence))),
        "memory_key": memory_key.strip() if memory_key else None,
        "exchange_ids": exchange_ids,
        "provenance": provenance,
        "evidence_sources": evidence_sources,
        "provenance_verified": provenance_verified,
        "conflict": bool(item.get("conflict", False)),
    }


def _merge_candidates(candidates: list[dict], event: bool = False) -> list[dict]:
    """Merge exact/keyed repeats and mark unresolved keyed contradictions."""
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for item in candidates:
        normalized = " ".join(item["content"].lower().split())
        key = (item["category"], item["memory_key"] or normalized)
        grouped[key].append(item)

    merged = []
    for (_category, _key), items in grouped.items():
        by_content: dict[str, list[dict]] = defaultdict(list)
        for item in items:
            by_content[" ".join(item["content"].lower().split())].append(item)
        contradictory = len(by_content) > 1 and any(item["memory_key"] for item in items)
        for same_content in by_content.values():
            chosen = max(same_content, key=lambda item: (item["exchange_ids"][-1], item["provenance"] == "USER_STATED"))
            chosen = dict(chosen)
            chosen["exchange_ids"] = sorted({exchange_id for item in same_content for exchange_id in item["exchange_ids"]})
            chosen["evidence_sources"] = [
                source for item in same_content for source in item["evidence_sources"]
            ]
            chosen["conflict"] = chosen["conflict"] or contradictory
            if contradictory:
                chosen["conflicting_contents"] = sorted(
                    item["content"] for content_items in by_content.values() for item in content_items
                    if item["content"] != chosen["content"]
                )
            merged.append(chosen)
    return sorted(merged, key=lambda item: (item["exchange_ids"][0], item["category"], item["content"].lower()))


def extract_plan(
    exchanges: list[dict], ask: Callable[[str], str | dict] = _ask_historical_extraction,
    batch_size: int = BATCH_SIZE,
) -> dict:
    """Classify exchange batches and build a review-only report in memory."""
    candidate_memories, candidate_events, skipped, errors = [], [], [], []
    for start in range(0, len(exchanges), batch_size):
        batch = exchanges[start:start + batch_size]
        exchanges_by_id = {item["id"]: item for item in batch}
        allowed_ids = set(exchanges_by_id)
        try:
            response = _json_object(ask(build_extraction_prompt(batch)))
        except (ValueError, TypeError, json.JSONDecodeError, brain.OpenAIStructuredOutputError) as exc:
            errors.append({"exchange_ids": sorted(allowed_ids), "reason": str(exc)})
            continue
        for raw in response.get("candidate_memories", []):
            item = _candidate(raw, exchanges_by_id)
            if item is not None:
                candidate_memories.append(item)
        for raw in response.get("candidate_events", []):
            item = _candidate(raw, exchanges_by_id, event=True)
            if item is not None:
                candidate_events.append(item)
        for raw in response.get("review_only_candidates", []):
            item = _candidate(raw, exchanges_by_id)
            if item is not None:
                candidate_memories.append(item)
        for raw in response.get("skipped_or_low_value", []):
            if isinstance(raw, dict):
                ids = _valid_ids(raw.get("exchange_ids"), allowed_ids)
                reason = raw.get("reason")
                if ids and isinstance(reason, str) and reason.strip():
                    skipped.append({"exchange_ids": ids, "reason": reason.strip()})

    def report_item(item: dict) -> dict:
        metadata = {
            "exchange_ids": item["exchange_ids"], "evidence_sources": item["evidence_sources"],
            "provenance": item["provenance"], "conflict": item["conflict"],
        }
        if item.get("conflicting_contents"):
            metadata["conflicting_contents"] = item["conflicting_contents"]
        return {
            "category": item["category"], "content": item["content"],
            "importance": item["importance"], "confidence": item["confidence"],
            "memory_key": item["memory_key"], "source": SOURCE_NAME,
            "source_id": ",".join(str(value) for value in item["exchange_ids"]),
            "metadata": metadata,
        }

    merged_memories = _merge_candidates(candidate_memories)
    primary = [item for item in merged_memories if item["provenance"] == "USER_STATED" and item["provenance_verified"]]
    review = [item for item in merged_memories if item not in primary]
    merged_events = _merge_candidates(candidate_events, event=True)
    primary_events = [item for item in merged_events
                      if item["provenance"] == "USER_STATED" and item["provenance_verified"]]
    review_events = [item for item in merged_events if item not in primary_events]
    def review_item(item: dict) -> dict:
        result = report_item(item)
        result["review_reason"] = (
            "user provenance could not be verified" if not item["provenance_verified"]
            else "not eligible for automatic durable memory: " + item["provenance"]
        )
        return result
    memories = [report_item(item) for item in primary]
    events = [report_item(item) for item in primary_events]
    review_items = [review_item(item) for item in review + review_events]
    return {
        "source_database": str(Path(config.DB_PATH).resolve()),
        "total_exchanges": len(exchanges),
        "candidate_memories": memories,
        "candidate_events": events,
        "review_only_candidates": review_items,
        "skipped_or_low_value": {"count": len(skipped), "reasons": skipped},
        "contradictions": [item for item in memories + events if item["metadata"]["conflict"]],
        "extraction_errors": errors,
    }


def generate_report(
    output_path: str = None, ask: Callable[[str], str | dict] = _ask_historical_extraction,
) -> tuple[dict, Path]:
    """Read exchanges, create a plan, and write only the requested JSON artifact."""
    exchanges = load_exchanges()
    report = extract_plan(exchanges, ask=ask)
    destination = Path(output_path) if output_path else Path(config.DB_PATH).parent / DEFAULT_REPORT_NAME
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report, destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="JSON review report path; defaults under Sheila's ignored data directory")
    args = parser.parse_args()
    try:
        report, destination = generate_report(args.output)
    except (OSError, sqlite3.Error) as exc:
        print("Historical extraction failed: " + str(exc))
        return 1
    print("Source database: " + report["source_database"])
    print("Exchanges examined: " + str(report["total_exchanges"]))
    print("Candidate memories: " + str(len(report["candidate_memories"])))
    print("Candidate events: " + str(len(report["candidate_events"])))
    print("Skipped/low value: " + str(report["skipped_or_low_value"]["count"]))
    print("Contradictions: " + str(len(report["contradictions"])))
    print("Extraction errors: " + str(len(report["extraction_errors"])))
    print("Review report: " + str(destination.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
