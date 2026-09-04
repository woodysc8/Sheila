"""Sheila's OpenAI-only reasoning boundary.

The LangGraph workflow supplies any bounded Google results as ``context``.
This module deliberately has no provider fallback: Sheila either uses OpenAI
or returns a clear configuration/request error.
"""

import requests

import config
import memory
import knowledge


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
OPENAI_NOT_CONFIGURED = "OpenAI is not configured. Add the OpenAI API key to the environment before using Sheila."
OPENAI_REQUEST_FAILED = "OpenAI couldn't respond right now. Check the API configuration and connection, then try again."


class Brain:
    """Unified facade over structured memory, conversation memory, and documents."""

    def remember(self, category: str, content: str, source: str, source_id: str = None,
                 importance: int = 0, metadata: dict = None) -> dict:
        return memory.remember(category, content, source, source_id, importance, metadata)

    def recall(self, query: str = "", category: str = None, limit: int = 10) -> list[dict]:
        return memory.recall(query, category, limit)

    def update(self, memory_id: int, **changes) -> dict:
        return memory.update(memory_id, **changes)

    def forget(self, memory_id: int) -> bool:
        return memory.forget(memory_id)

    def search_documents(self, question: str, top_k: int = 4) -> str:
        return knowledge.search_documents(question, top_k=top_k)

    def ingest_document(self, doc_id: str, text: str, source_name: str):
        return knowledge.ingest_document(doc_id, text, source_name)

    def get_memory_context(self, query: str = "") -> str:
        return f"{memory.get_context(current_query=query)}\n\n{memory.get_structured_context(query=query)}"


brain = Brain()


def _configured_api_key() -> str:
    key = config.OPENAI_API_KEY.strip()
    if not key or "PUT_YOUR" in key.upper():
        return ""
    return key


def _response_text(payload: dict) -> str:
    """Extract text from a Responses API payload without assuming one shape."""
    text = str(payload.get("output_text", "")).strip()
    if text:
        return text
    for output in payload.get("output", []):
        for content in output.get("content", []):
            if content.get("type") in {"output_text", "text"}:
                text = str(content.get("text", "")).strip()
                if text:
                    return text
    return ""


def _ask_openai(prompt: str) -> str:
    key = _configured_api_key()
    if not key:
        return OPENAI_NOT_CONFIGURED
    try:
        response = requests.post(
            OPENAI_RESPONSES_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": config.OPENAI_MODEL,
                "instructions": config.SYSTEM_PROMPT,
                "input": prompt,
                "max_output_tokens": 400,
            },
            timeout=30,
        )
        response.raise_for_status()
        return _response_text(response.json()) or OPENAI_REQUEST_FAILED
    except requests.RequestException as exc:
        print(f"[brain] OpenAI request failed: {exc}")
        return OPENAI_REQUEST_FAILED
    except (TypeError, ValueError, KeyError) as exc:
        print(f"[brain] OpenAI response could not be read: {exc}")
        return OPENAI_REQUEST_FAILED


def summarize_message(sender_name: str, context_label: str, body: str) -> str:
    """Summarize a user-requested message with the same OpenAI-only provider."""
    prompt = f"""Summarize this message in 2-3 short spoken sentences, as if
telling the user what it says. Be direct and conversational, no preamble.

From: {sender_name}
Context: {context_label}
Content: {body[:1500]}
"""
    return _ask_openai(prompt)


def think(user_text: str, context: str = "") -> str:
    """Use OpenAI to respond while preserving the established call signature."""
    memory_context = brain.get_memory_context(query=user_text)
    pending = memory.get_pending_notifications()
    if pending:
        memory_context = f"{memory_context}\n\nPending notifications:\n" + "\n".join(
            f"- [{ts}] {source}: {summary}" for ts, source, summary in pending
        )
    prompt = f"""Source-grounding rules:
- Treat source labels as evidence boundaries, not permission to invent facts.
- When an integration result is supplied, it is the source of truth for factual claims about that service. Never invent or contradict records, and never claim zero results when the labeled results contain records.
- For calendar-only questions, [CALENDAR RESULTS] is authoritative for its stated range: describe only its events, preserve their exact dates/times, and do not present Gmail, Drive, memory, or general knowledge as calendar events.
- Never use old memory as a substitute for current API results. Clearly distinguish an unavailable integration from a successful zero-result query.
- A Drive document mention or folder association does not prove a company is a client, account, or customer. State that evidence is insufficient unless the retrieved Drive text explicitly identifies that relationship.
- Do not describe old Gmail results as current or recent. Preserve retrieved dates exactly when available.
- If evidence is insufficient, say so plainly. Do not infer missing relationships, dates, or events.

Context from memory:
{memory_context}

Read-only information retrieved for this request:
{context if context else "No external information was retrieved."}

Current request from the user: {user_text}"""
    return _ask_openai(prompt)
