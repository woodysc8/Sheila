"""
Records audio from the mic while the push-to-talk key is held, then
transcribes with faster-whisper (runs locally, no API cost).
"""

import config

try:
    import sounddevice as sd
except Exception:
    sd = None

try:
    import numpy as np
except Exception:
    np = None

try:
    import keyboard
except Exception:
    keyboard = None

try:
    from faster_whisper import WhisperModel
except Exception:
    WhisperModel = None

_model = None
PTT_KEY = "\\"  # backslash key — change here if you ever want a different key


def _get_model():
    global _model
    if WhisperModel is None:
        raise RuntimeError("faster-whisper is not installed")
    if _model is None:
        _model = WhisperModel("base", device="cpu", compute_type="int8")
    return _model

def record_while_held() -> str:
    """Blocks until PTT_KEY is pressed, records while held, stops on release.
    If no microphone or audio stack is available, falls back to typed input so
    the assistant remains usable in a headless or mic-less environment."""
    if keyboard is None:
        raise RuntimeError("keyboard package is not installed")

    if sd is None or np is None:
        print("[stt] No microphone/audio stack available, falling back to typed input.")
        return input("[stt] Type your message: ").strip()

    print(f"[stt] Hold \\ to talk...")
    keyboard.wait(PTT_KEY)

    print("[stt] Recording... (release to stop)")
    frames = []

    def callback(indata, frame_count, time_info, status):
        frames.append(indata.copy())

    stream = sd.InputStream(
        samplerate=config.SAMPLE_RATE,
        channels=1,
        dtype="int16",
        callback=callback,
    )

    with stream:
        while keyboard.is_pressed(PTT_KEY):
            sd.sleep(50)

    if not frames:
        return ""

    audio_np = np.concatenate(frames, axis=0).flatten().astype(np.float32) / 32768.0

    print("[stt] Transcribing...")
    model = _get_model()
    segments, _ = model.transcribe(audio_np, beam_size=5, language="en")
    text = " ".join(seg.text for seg in segments).strip()
    print(f"[stt] Heard: {text}")
    return text