"""
Brain: tries Gemini keys in order, remembers dead ones so it doesn't
waste time retrying them. Falls back to Claude, then Perplexity, if all
Gemini keys are exhausted.
Dead keys are remembered for 24 hours, then retried automatically.
"""
from google import genai
from google.genai import types
import requests
import time
import config
import memory
import knowledge

# Tracks when each key died: {"key": timestamp}
_dead_keys: dict[str, float] = {}
DEAD_KEY_RETRY_SECONDS = 86400  # 24 hours


def _key_is_dead(key: str) -> bool:
    if key not in _dead_keys:
        return False
    if time.time() - _dead_keys[key] > DEAD_KEY_RETRY_SECONDS:
        del _dead_keys[key]
        print(f"[brain] Key ...{key[-6:]} has been dead 24h, retrying it.")
        return False
    return True


def _mark_dead(key: str):
    _dead_keys[key] = time.time()
    print(f"[brain] Marking key ...{key[-6:]} as dead for 24 hours.")


def _try_gemini(prompt: str) -> str | None:
    for key in config.GEMINI_API_KEYS:
        if not key or "PUT_YOUR" in key:
            continue
        if _key_is_dead(key):
            print(f"[brain] Skipping dead key ...{key[-6:]}")
            continue
        try:
            client = genai.Client(api_key=key)
            response = client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=config.SYSTEM_PROMPT,
                    max_output_tokens=200,
                ),
            )
            return response.text.strip()
        except Exception as e:
            err = str(e)
            print(f"[brain] Gemini key ...{key[-6:]} error: {err[:120]}")
            if any(code in err for code in ["429", "RESOURCE_EXHAUSTED", "API_KEY_INVALID", "403", "400"]):
                _mark_dead(key)
                continue
            raise
    return None


def _try_perplexity(prompt: str) -> str | None:
    key = config.PERPLEXITY_API_KEY
    if not key or "PUT_YOUR" in key:
        return None
    try:
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        body = {
            "model": "sonar",
            "messages": [
                {"role": "system", "content": config.SYSTEM_PROMPT + "\n\nIMPORTANT: The user's message includes conversation history and personal context above the actual question. Prioritize that context over doing a fresh web search -- only search the web if the context genuinely doesn't answer the question. Do not include citation brackets like [1] in your response."},
                {"role": "user", "content": prompt},
            ],
        }
        r = requests.post("https://api.perplexity.ai/chat/completions", headers=headers, json=body, timeout=30)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[brain] Perplexity failed: {e}")
        return None


def _try_claude(prompt: str) -> str | None:
    key = config.ANTHROPIC_API_KEY
    if not key or "PUT_YOUR" in key:
        return None
    if not key.startswith("sk-ant-"):
        print("[brain] Claude key does not look like an Anthropic key; skipping.")
        return None
    try:
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        body = {
            "model": "claude-3-5-haiku-20241022",
            "max_tokens": 400,
            "system": config.SYSTEM_PROMPT[:1200],
            "messages": [{"role": "user", "content": prompt[:1200]}],
        }
        r = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=body, timeout=30)
        r.raise_for_status()
        return r.json()["content"][0]["text"].strip()
    except Exception as e:
        print(f"[brain] Claude failed: {e}")
        return None


def summarize_message(sender_name: str, context_label: str, body: str) -> str:
    """One-off summarization for 'what did X say' follow-ups (works for both
    email and Slack). Uses the same fallback chain as think(), but a focused
    prompt instead of full memory context -- this is a deliberate,
    user-initiated call, not part of the zero-cost polling paths."""
    prompt = f"""Summarize this message in 2-3 short spoken sentences, as if
telling the user what it says. Be direct and conversational, no preamble.

From: {sender_name}
Context: {context_label}
Content: {body[:1500]}
"""
    result = _try_gemini(prompt)
    if result:
        return result
    result = _try_claude(prompt)
    if result:
        return result
    result = _try_perplexity(prompt)
    if result:
        return result
    return f"I have the message from {sender_name} logged, but couldn't summarize it right now."


def think(user_text: str) -> str:
    context = memory.get_context(current_query=user_text)
    pending = memory.get_pending_notifications()
    if pending:
        context = f"{context}\n\nPending notifications:\n" + "\n".join(
            f"- [{ts}] {source}: {summary}" for ts, source, summary in pending
        )
    doc_context = knowledge.query(user_text)
    prompt = f"""Context from memory:
{context}

Relevant information from your documents (User Background, StreetCred Sourcebook, etc.):
{doc_context if doc_context else "Nothing relevant found in your documents."}

Current request from the user: {user_text}"""

    result = _try_gemini(prompt)
    if result:
        return result

    print("[brain] All Gemini keys exhausted, trying Claude...")
    result = _try_claude(prompt)
    if result:
        print("[brain] Responded via Claude.")
        return result

    print("[brain] Claude failed, trying Perplexity...")
    result = _try_perplexity(prompt)
    if result:
        print("[brain] Responded via Perplexity.")
        return result

    if not any(
        key and "PUT_YOUR" not in key for key in config.GEMINI_API_KEYS
    ) and not config.ANTHROPIC_API_KEY.strip() and not config.PERPLEXITY_API_KEY.strip():
        return "I’m running in offline mode right now because no valid AI keys are configured. I can still log your requests and help once the connection is available."

    return "I’m having trouble reaching my AI backends right now, but I’m still here and ready to help once the connection is restored."
