"""
Iris — desktop push-to-talk version.

Loop:
  1. Hold the PTT key (see stt.py), talk, release
  2. Transcribed locally (faster-whisper)
  3. Sent to Gemini with memory context
  4. Response spoken aloud
  5. Exchange logged to memory

Run with: python main.py
Ctrl+C to stop.
"""

import re
import stt
import tts
import brain
import memory
import briefing

REMEMBER_PHRASES = ["remember this", "remember that", "note that down", "don't forget this"]
FORGET_PHRASES = ["forget that", "forget this", "delete that", "never mind that"]
CATCHUP_PHRASES = ["catch me up", "fill me in", "what did i miss", "what happened while"]
MEETING_START_PHRASES = ["i'm in a meeting", "im in a meeting", "going into a meeting", "start meeting mode"]
MEETING_END_PHRASES = ["meeting's over", "meeting is over", "i'm out of my meeting", "end meeting mode"]
SHUT_UP_PHRASES = ["shut up", "hush", "quiet please", "pipe down", "zip it"]
GOOD_MORNING_PHRASES = [
    "good morning",
    "morning iris",
    "morning, iris",
    "good morning iris",
    "morning",
    "hi iris",
    "hello iris",
    "hey iris",
]


FOLLOWUP_PATTERN = re.compile(r"what did ([\w\s]+?) (say|email|write|send)", re.IGNORECASE)
EMAIL_QUERY_PATTERN = re.compile(r"(email|emails|mail)(s)?\s+(from|received|i received|i got|today|that came in)", re.IGNORECASE)


def _handle_followup(name_query: str) -> str:
    row = memory.get_latest_message_from(name_query)
    if row is None:
        return f"I don't have anything recent from {name_query}."
    _ts, sender_name, context_label, body, _platform = row
    return brain.summarize_message(sender_name, context_label, body)


def _handle_catchup() -> str:
    pending = memory.get_pending_notifications()
    if not pending:
        return "Nothing came up while you were away. All quiet."
    lines = [f"{summary}" for (_ts, _source, summary) in pending]
    memory.mark_notifications_delivered()
    if len(lines) == 1:
        return f"One thing came up: {lines[0]}"
    return f"{len(lines)} things came up. " + " Also, ".join(lines)


def _handle_email_query() -> str:
    pending = memory.get_pending_notifications()
    if not pending:
        return "I don't have any recent email activity recorded right now."
    lines = [f"{summary}" for (_ts, _source, summary) in pending]
    memory.mark_notifications_delivered()
    if len(lines) == 1:
        return f"The latest email activity was: {lines[0]}"
    return "Recent email activity includes: " + "; ".join(lines)


def main():
    memory.init_db()
    print("Iris is online. If a microphone is available, hold the PTT key to talk; otherwise you can type your message. Ctrl+C to quit.")

    while True:
        try:
            user_text = stt.record_while_held()

            if not user_text.strip():
                print("[main] Didn't catch anything, try again.")
                continue

            lowered = user_text.lower()

            if any(p in lowered for p in SHUT_UP_PHRASES):
                memory.set_meeting_status(True)
                reply = "Understood. Going quiet -- say 'catch me up' whenever you're ready."
                tts.speak(reply)
                memory.log_exchange(user_text, reply)
                continue

            if any(p in lowered for p in GOOD_MORNING_PHRASES):
                if lowered in {"morning", "morning iris", "morning, iris"}:
                    lowered = "good morning"
                memory.set_meeting_status(False)  # in case it was left on overnight
                brief = briefing.build_morning_briefing()
                reply = f"Good morning. {brief}"
                tts.speak(reply)
                memory.log_exchange(user_text, reply)
                continue

            if any(p in lowered for p in MEETING_START_PHRASES):
                memory.set_meeting_status(True)
                reply = "Understood. I'll hold everything until you're out."
                tts.speak(reply)
                memory.log_exchange(user_text, reply)
                continue

            if any(p in lowered for p in MEETING_END_PHRASES):
                memory.set_meeting_status(False)
                reply = "Welcome back. Say the word if you'd like me to catch you up."
                tts.speak(reply)
                memory.log_exchange(user_text, reply)
                continue

            if any(p in lowered for p in CATCHUP_PHRASES):
                reply = _handle_catchup()
                tts.speak(reply)
                memory.log_exchange(user_text, reply)
                continue

            if EMAIL_QUERY_PATTERN.search(user_text):
                reply = _handle_email_query()
                tts.speak(reply)
                memory.log_exchange(user_text, reply)
                continue

            followup_match = FOLLOWUP_PATTERN.search(user_text)
            if followup_match:
                name_query = followup_match.group(1).strip()
                reply = _handle_followup(name_query)
                tts.speak(reply)
                memory.log_exchange(user_text, reply)
                continue

            reply = brain.think(user_text)
            tts.speak(reply)

            if any(p in lowered for p in FORGET_PHRASES):
                memory.forget_last()
                print("[main] Last exchange forgotten.")
                continue

            important = any(p in lowered for p in REMEMBER_PHRASES)
            memory.log_exchange(user_text, reply, important=important)

        except KeyboardInterrupt:
            print("\nShutting down.")
            break
        except Exception as e:
            print(f"[main] Error: {e}")


if __name__ == "__main__":
    main()
