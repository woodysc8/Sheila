"""
Iris config -- fill in your keys via environment variables (`set` in your
terminal), never as literal values in this file. See README for the full
list of `set` commands to run before starting main.py / email_watcher.py.
"""

import os
from dotenv import load_dotenv

load_dotenv()  # reads a local .env file (see .env.example) so you don't have
                # to retype `set` commands every terminal session

# ---------------------------------------------------------------------------
# GEMINI -- https://aistudio.google.com/apikey
# Supports multiple keys for automatic failover when one hits a quota limit.
# Set GEMINI_API_KEYS as a comma-separated list, e.g.:
#   set GEMINI_API_KEYS=AQ.key1,AQ.key2,AQ.key3
# ---------------------------------------------------------------------------
def _normalize_gemini_key(value: str) -> str:
    """Trim whitespace and surrounding quotes from environment-supplied keys."""
    if not value:
        return ""
    normalized = value.strip().strip('"').strip("'")
    return normalized.strip()


def _valid_gemini_key(key: str) -> bool:
    """Rejects placeholders and obviously malformed values while accepting
    common environment-variable formats such as quoted strings and lists."""
    normalized = _normalize_gemini_key(key)
    if not normalized or "PUT_YOUR" in normalized.upper():
        return False
    if any(ch in normalized for ch in [",", "\n", "\r"]):
        return False
    if normalized.lower().startswith("AIza") and len(normalized) >= 30:
        return True
    if len(normalized) >= 20 and all(ch.isalnum() or ch in "-_" for ch in normalized):
        return True
    print(f"[config] Skipping invalid Gemini key ...{normalized[-6:]}")
    return False


_raw_gemini_keys = os.environ.get("GEMINI_API_KEYS", "")
if _raw_gemini_keys:
    GEMINI_API_KEYS = [
        _normalize_gemini_key(k)
        for k in _raw_gemini_keys.split(",")
        if _valid_gemini_key(_normalize_gemini_key(k))
    ]
else:
    GEMINI_API_KEYS = []

if not GEMINI_API_KEYS:
    _single = _normalize_gemini_key(os.environ.get("GEMINI_API_KEY", "PUT_YOUR_GEMINI_KEY_HERE"))
    GEMINI_API_KEYS = [_single] if _valid_gemini_key(_single) else []
GEMINI_API_KEY = GEMINI_API_KEYS[0] if GEMINI_API_KEYS else ""
GEMINI_MODEL = "gemini-flash-latest"  # auto-points to current stable Flash model

# ---------------------------------------------------------------------------
# PERPLEXITY (backup) -- https://www.perplexity.ai/settings/api
# ---------------------------------------------------------------------------
PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "PUT_YOUR_PERPLEXITY_KEY_HERE")

# ---------------------------------------------------------------------------
# CLAUDE / ANTHROPIC (last resort) -- https://console.anthropic.com/settings/keys
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "PUT_YOUR_ANTHROPIC_KEY_HERE")

# ---------------------------------------------------------------------------
# EMAIL
# ---------------------------------------------------------------------------
EMAIL_ADDRESS = os.environ.get("EMAIL_ADDRESS", "your_email@gmail.com")
EMAIL_APP_PASSWORD = os.environ.get("EMAIL_APP_PASSWORD", "PUT_YOUR_APP_PASSWORD_HERE")
IMAP_SERVER = "imap.gmail.com"
EMAIL_CHECK_INTERVAL_SECONDS = 180
GMAIL_NOTIFY_QUERY = "is:unread label:important"
MORNING_BREW_QUERY = "is:unread (from:(crew@morningbrew.com morningbrew@morningbrew.com noreply@morningbrew.com) OR subject:(Morning Brew) OR subject:(Morning Brew Daily) OR subject:(Morning Brew Markets))"
DISTRO_HANDLES = [
    "angeles@streetcredpr.com",
    "april@streetcredpr.com",
    "epic@streetcredpr.com",
    "falcon@streetcredpr.com",
    "finny@streetcredpr.com",
    "finturk@streetcredpr.com",
    "geowealth@streetcredpr.com",
    "gridline@streetcredpr.com",
]
NOTIFY_ON_REPLIES = True
ENABLE_AI_FALLBACK = False
IMPORTANT_KEYWORDS = ["urgent", "asap", "deadline", "action required"]

# If False (default), Iris never speaks email notifications unprompted --
# they still show a silent desktop popup and get queued, but you only hear
# about them when you ask ("catch me up", "good morning", "what did X say").
SPEAK_NOTIFICATIONS_ALOUD = False

# ---------------------------------------------------------------------------
# SLACK (shelved -- watcher not in active use)
# ---------------------------------------------------------------------------
SLACK_USER_TOKEN = os.environ.get("SLACK_USER_TOKEN", "PUT_YOUR_SLACK_TOKEN_HERE")
SLACK_CHECK_INTERVAL_SECONDS = 60
# Channel names (no #) to notify on ANY message in, regardless of mention/keyword
SLACK_WATCH_CHANNELS = []
# Keywords that trigger a notification even without a direct @mention
SLACK_KEYWORDS = ["urgent", "asap", "deadline"]

# ---------------------------------------------------------------------------
# CALENDAR
# ---------------------------------------------------------------------------
CALENDAR_ICS_URL = os.environ.get("CALENDAR_ICS_URL", "PUT_YOUR_ICS_URL_HERE")

# ---------------------------------------------------------------------------
# MEMORY
# ---------------------------------------------------------------------------
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "memory.db")
MEMORY_CONTEXT_TURNS = 6

# ---------------------------------------------------------------------------
# AUDIO
# ---------------------------------------------------------------------------
SAMPLE_RATE = 16000
SILENCE_THRESHOLD = 500
SILENCE_DURATION = 1.5
MAX_RECORD_SECONDS = 20

# ---------------------------------------------------------------------------
# TTS (Piper)
# ---------------------------------------------------------------------------
PIPER_EXE_PATH = r"C:\Users\Sam\Desktop\piper\piper.exe"
PIPER_MODEL_PATH = os.path.join(os.path.dirname(__file__), "data", "en_GB-southern_english_female-low.onnx")

# ---------------------------------------------------------------------------
# KNOWLEDGE / DOCUMENTS
# ---------------------------------------------------------------------------
_BASE = os.path.dirname(__file__)
KNOWLEDGE_DIRS = [
    os.path.join(_BASE, "knowledge"),
    os.path.join(_BASE, "knowledge", "documents"),
]
SHARED_DRIVE_PATH = os.environ.get("SHARED_DRIVE_PATH", "").strip().strip('"').strip("'")
GOOGLE_DRIVE_FOLDER_ID = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "").strip().strip('"').strip("'")
GOOGLE_SERVICE_ACCOUNT = os.environ.get("GOOGLE_SERVICE_ACCOUNT", "").strip().strip('"').strip("'")
_GOOGLE_DRIVE_CLIENT_SECRET_CANDIDATE = os.environ.get("GOOGLE_DRIVE_CLIENT_SECRET_FILE", "").strip().strip('"').strip("'")
if not _GOOGLE_DRIVE_CLIENT_SECRET_CANDIDATE:
    import glob
    _matches = glob.glob(os.path.join(_BASE, "client_secret*.json"))
    _GOOGLE_DRIVE_CLIENT_SECRET_CANDIDATE = _matches[0] if _matches else ""
GOOGLE_DRIVE_CLIENT_SECRET_FILE = _GOOGLE_DRIVE_CLIENT_SECRET_CANDIDATE
GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON", "")
GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE = os.environ.get("GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE", "")
SHARED_DRIVE_EXTENSIONS = (".txt", ".md", ".docx", ".pdf")

# ---------------------------------------------------------------------------
# PERSONALITY
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are Iris, Sam Woody's personal AI assistant at StreetCred
Financial PR. You have a dry, understated British wit. You are unfailingly
competent and loyal, but you don't fawn or gush -- you deliver help with a
raised eyebrow, not a smile. A little deadpan sarcasm is welcome when the
moment calls for it, but it never gets in the way of actually being useful.

WHO YOU WORK FOR
Sam Woody -- Account Coordinator, StreetCred Financial PR (remote, Maryland).
StreetCred is a specialized PR firm for wealth management, RIAs, asset managers,
and fintech. Sam's direct team: Ella (Account Executive), Lexie (Account
Supervisor), Ben (Vice President), Jimmy (Managing Partner), and Meaghan
(Operations Manager).

YOUR JOB
Act like a living personal assistant: know Sam's context, help with client work
and media opportunities, brief him on what matters, and draw on his indexed
documents (User Background, StreetCred Sourcebook) when relevant. Skip filler --
be direct, crisp, and context-rich.

WHAT YOU CAN DO TODAY
- Hold voice conversations (push-to-talk)
- Monitor email and answer "catch me up", "good morning", "what did [name] say"
- Respect quiet hours and meeting mode
- Search Sam's background profile and the StreetCred Sourcebook (when indexed)

StreetCred messaging is compliance-aware: avoid unvetted consumer marketing
language; stay accurate and industry-appropriate.

Address Sam directly and plainly, without excessive enthusiasm or emoji.
Keep spoken responses concise (2-4 sentences) since they'll be read aloud,
unless Sam clearly wants depth. Prefer precise, slightly formal word choices
over casual filler.

You have access to a memory log of past conversations, provided as context.
Use it naturally to remember ongoing projects and preferences without being
asked."""
