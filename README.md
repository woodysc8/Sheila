# Sheila — Desktop Push-to-Talk Assistant

The simplest version: no Pi, no wake word, no Alexa hacking — just your
computer, your professional mic, and your speaker. Hold SPACE, talk, release,
get a spoken answer. This proves out the core "talk to an AI" loop with the
least amount of stuff that can go wrong, and it's the same code you'll
extend later.

## What you need
- Your existing mic + speaker (already connected — no new hardware)
- A free Gemini API key: https://aistudio.google.com/apikey

## Setup

```bash
cd jarvis-desktop
pip install -r requirements.txt

# Set your API key
export GEMINI_API_KEY="your_key_here"
```

**Note on the `keyboard` library:** on Linux it needs root to capture global
key presses (`sudo python main.py`). On Windows/Mac it should work without
elevated permissions in most setups.

### TTS — pick one
- **Quick start (zero setup):** in `main.py`, use `tts.speak_fallback(reply)`
  instead of `tts.speak(reply)`. Uses `pyttsx3` — works immediately, robotic
  voice.
- **Better quality:** set up Piper (see below), keep `tts.speak(reply)`.

```bash
# Piper setup (optional, better voice quality)
# 1. Download the piper binary for your OS: https://github.com/rhasspy/piper/releases
# 2. Download a voice model, e.g. en_US-lessac-medium.onnx (+ its .json)
# 3. Place it at jarvis-desktop/data/voice.onnx
mkdir -p data
```

## Running it

```bash
python main.py
```

Hold **SPACE**, talk, release. It'll transcribe what you said, send it to
Gemini along with your personality prompt and any memory context, then speak
the reply back and log the exchange.

## Why push-to-talk first, not wake word

Wake word detection is the part most likely to eat your first few hours in
false positives and tuning — worth adding once the core loop already feels
good, not before. Push-to-talk with a real mic should just work.

## Tuning
- **Whisper model size** (`stt.py`, `"base"`) — bump to `"small"` for better
  accuracy; a pro mic should make that upgrade noticeably better rather than
  marginal.
- **Personality** — edit `SYSTEM_PROMPT` in `config.py` any time, no code
  changes needed elsewhere.
- **"Remember this"** — say it in a request and that exchange gets tagged
  important, always included in future context (see `REMEMBER_PHRASES` in
  `main.py` to add your own trigger words).

## Google Drive workaround (live API)
If you already have a live Google API connection, Sheila can index a shared Drive
folder directly instead of relying only on a local sync folder.

Set these environment variables before running the ingest step:

```bash
set GOOGLE_DRIVE_FOLDER_ID=your_shared_folder_id
set GOOGLE_DRIVE_CLIENT_SECRET_FILE=C:\path\to\client_secret_xxx.json
# or, if you are using the service-account route instead
set GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON={"type":"service_account",...}
# or
set GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE=C:\path\to\service-account.json
set SHARED_DRIVE_PATH=C:\path\to\synced\company\drive
```

Then reindex:

```bash
python ingest.py
```

That makes Sheila pull the Drive files into the same local knowledge vector store
it already uses for background questions and client context.

## Second Brain storage

The Second Brain keeps two deliberately separate stores: SQLite contains
conversation exchanges and structured memories (facts, preferences, people,
projects, and decisions), while Chroma contains document chunks and Gemini
embeddings. The `Brain` facade in `brain.py` provides the common interface
without flattening documents into structured memory.

Each structured memory records its category, provenance source, optional source
identifier, importance, timestamps, and JSON metadata. Existing `exchanges`
conversation history is retained. Retrieval is bounded and uses simple
structured/text matching; it does not add an LLM call.

### Render persistence warning

`config.py` stores SQLite at `data/memory.db` and Chroma at `data/chroma`.
These are local filesystem paths, and this repository has no Render persistent
disk configuration. Render's ephemeral filesystem does not guarantee either
store survives a service restart, deploy, rebuild, or instance replacement.
The current deployment therefore must be treated as non-durable for memory and
document indexes. Attach and mount a Render persistent disk, or later provide
a durable SQLite/Chroma-compatible storage service, before relying on this data
in production. The Brain interface keeps that future backend migration local.

## What's next
Once this feels good day-to-day:
1. Swap SPACE-hold for wake-word detection (Picovoice Porcupine) — the
   earlier Pi scaffold has this ready to graft back in.
2. Move it onto a Raspberry Pi so it's always-on and doesn't need your work
   computer running.
3. Layer in email triage, the "explain this visually" screen hijack, and
   read-only financial queries — one at a time, once each prior piece feels
   solid.
