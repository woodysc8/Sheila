"""
Speaks text aloud using Piper (local TTS, free, and the one that actually
sounds British if you use the en_GB-alan-medium voice).

Requires the piper executable and a voice model -- see README for setup.
"""

import subprocess
import tempfile
import os
import re
import config

try:
    import soundfile as sf
    import sounddevice as sd
except Exception:
    sf = None
    sd = None

# Piper looks for its supporting files (espeak-ng-data, DLLs) relative to
# its own folder -- if it's launched from elsewhere, it crashes with a
# DLL_NOT_FOUND error. Running it with cwd set to its own folder fixes this.
_PIPER_DIR = os.path.dirname(config.PIPER_EXE_PATH)


def _sanitize_for_speech(text: str) -> str:
    """Strips citation markers and markdown formatting, normalizes
    smart-quote/dash Unicode to plain ASCII -- SAPI5 (via pyttsx3) can
    silently produce no audio at all on certain Unicode/markdown-heavy text,
    especially common in Perplexity/Claude responses."""
    text = re.sub(r'\[\d+\]', '', text)           # [1] [2] citations
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)   # **bold**
    text = re.sub(r'\*(.+?)\*', r'\1', text)       # *italic*
    text = re.sub(r'`(.+?)`', r'\1', text)         # `code`
    text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)  # # headers
    text = re.sub(r'^[-•]\s*', '', text, flags=re.MULTILINE)  # bullet points
    replacements = {
        '\u2018': "'", '\u2019': "'",
        '\u201c': '"', '\u201d': '"',
        '\u2013': '-', '\u2014': '-',
        '\u2026': '...',
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return text.strip()


def _speak_sentences(engine, text: str):
    """Speaks sentence-by-sentence, fully completing each one before moving
    to the next. Windows' SAPI5 driver has a known bug where queuing
    multiple say() calls before a single runAndWait() often only plays the
    FIRST utterance and silently drops the rest -- calling runAndWait()
    after each individual sentence avoids that."""
    sentences = re.split(r'(?<=[.!?])\s+', text)
    for sentence in sentences:
        if sentence.strip():
            engine.say(sentence.strip())
            engine.runAndWait()


def _piper_available() -> bool:
    if sf is None or sd is None:
        return False
    if not os.path.exists(config.PIPER_EXE_PATH):
        return False
    if not os.path.exists(config.PIPER_MODEL_PATH):
        return False
    return True


def speak(text: str):
    text = _sanitize_for_speech(text)
    print(f"[tts] Sheila: {text}")

    if not _piper_available():
        print("[tts] Piper not available, using fallback speech.")
        speak_fallback(text)
        return

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav_path = f.name

    try:
        subprocess.run(
            [config.PIPER_EXE_PATH, "--model", config.PIPER_MODEL_PATH, "--output_file", wav_path],
            input=text.encode("utf-8"),
            check=True,
            cwd=_PIPER_DIR,
        )
        data, samplerate = sf.read(wav_path)
        sd.play(data, samplerate)
        sd.wait()
    except Exception as e:
        print(f"[tts] Piper failed, using fallback speech: {e}")
        speak_fallback(text)
    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)


_fallback_engine = None
_fallback_call_count = 0
_ENGINE_REFRESH_INTERVAL = 8  # recreate the engine every N calls to avoid staleness


def speak_fallback(text: str):
    """Uses pyttsx3, which taps into Windows' built-in SAPI5 voices --
    including British ones (Hazel/George) if installed via Windows language
    settings.

    Known issue: pyttsx3's SAPI5 driver on Windows can silently stop
    producing audio after extended use in a long-running process (no error,
    it just goes quiet). Recreating the engine periodically works around it."""
    global _fallback_engine, _fallback_call_count
    import pyttsx3
    text = _sanitize_for_speech(text)
    print(f"[tts-fallback] Sheila: {text}")

    _fallback_call_count += 1
    needs_fresh_engine = (
        _fallback_engine is None
        or _fallback_call_count % _ENGINE_REFRESH_INTERVAL == 0
    )
    if needs_fresh_engine:
        if _fallback_engine is not None:
            try:
                _fallback_engine.stop()
            except Exception:
                pass
        _fallback_engine = pyttsx3.init()
        for voice in _fallback_engine.getProperty("voices"):
            name = voice.name.lower()
            if "gb" in name or "british" in name or "hazel" in name or "george" in name or "uk" in name:
                _fallback_engine.setProperty("voice", voice.id)
                break

    try:
        _speak_sentences(_fallback_engine, text)
    except Exception as e:
        print(f"[tts-fallback] Engine error, forcing a fresh one next call: {e}")
        _fallback_engine = None
