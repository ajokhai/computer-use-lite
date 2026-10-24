"""App data, settings, and API keys (kept in the macOS Keychain, never in the app bundle)."""

import json
import os
import subprocess
from pathlib import Path

APP_NAME = "Computer Use"
DATA = Path.home() / "Library" / "Application Support" / APP_NAME
LOG = Path.home() / "Library" / "Logs" / "Computer Use" / "tasks.log"  # rotated like app.log (cu/logger.py)
KEYCHAIN_SERVICE = "computer-use-lite"
KEYS = ("TYPESAFE_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY",
        "GITHUB_TOKEN")
KEY_INFO = {  # label, where to get it
    "TYPESAFE_API_KEY": ("Jev (TypeSafe)", "Cheapest actions. From your TypeSafe dashboard."),
    "ANTHROPIC_API_KEY": ("Claude (Anthropic)", "Vision model. platform.claude.com › API keys"),
    "OPENAI_API_KEY": ("OpenAI", "Vision model. platform.openai.com/api-keys"),
    "GEMINI_API_KEY": ("Gemini", "Vision model and Gemini voices. aistudio.google.com/apikey"),
    "OPENROUTER_API_KEY": ("OpenRouter", "Any model OpenRouter offers (pick it under Mode › Vision Model). openrouter.ai/keys"),
    "GITHUB_TOKEN": ("GitHub token", "Only for updates from a private repo when the gh CLI isn't signed in."),
}

DEFAULTS = {
    "mode": "auto",
    "awake": True,
    "confirm_all": False,
    "allow_risky": False,
    "auto_switch": True,
    "auto_update": True,  # install new releases when idle
    "last_version": "",
    "hands_free": False,  # always listening for "Computer, …"
    "shortcut": None,  # opens the bar; None = double-tap Control (see cu/shortcut.py)
    "open_listening": True,  # the bar starts listening when the shortcut opens it
    "recent_tasks": [],  # newest first; Up/Down in the bar steps through them
    "speak_replies": True,
    "narrate": False,  # talk through each step while working
    "speaking_voice": "mac:",  # "mac:<name>" (empty = system voice), "kokoro:<id>", or "gemini:<name>" (see cu/say.py)
    "speech_engine": "apple",  # "apple" or a model id like "moonshine:base" (see cu/stt.py)
    "custom_speech_models": [],  # model ids added with Voice Engine › Add Custom Model…
    # The vision model plans (Vision + Jev) and does vision computer use (Vision Only).
    # "auto" = the first provider in vision_order with a key and quota left; or a model id
    # from one of the lists below, "openrouter:<model>" or "local:<model>" (see cu/vision/).
    "vision_model": "auto",
    "vision_order": ["claude", "openai", "gemini", "openrouter", "local"],
    "claude_models": ["claude-opus-5-5", "claude-sonnet-5-5"],
    "openai_models": ["gpt-6.1-sol"],
    "gemini_models": ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3-flash-preview"],
    "openrouter_model": "",  # e.g. a vision model's OpenRouter id; empty = OpenRouter unused
    "local_model": "",  # model name on the local server; empty = local unused
    "local_url": "http://localhost:11434/v1",  # any OpenAI-compatible server (Ollama, LM Studio…)
    # Neither provider exposes remaining quota, so these come from your dashboards.
    # None means "unknown": usage is still counted, just not shown against a limit.
    "limits": {
        "gemini-3.8-flash": {"requests_per_day": None},
        "gemini-3.5-flash-lite": {"requests_per_day": None},
        "gemini-3-flash-preview": {"requests_per_day": None},
        "jev": {"usd_per_month": None, "reset_day": 1},
    },
}


def load_settings() -> dict:
    try:
        saved = json.loads((DATA / "settings.json").read_text())
    except (OSError, ValueError):
        saved = {}
    merged = {**DEFAULTS, **saved}
    merged["limits"] = {**DEFAULTS["limits"], **saved.get("limits", {})}
    if merged["mode"] == "gemini":  # modes are named by role now
        merged["mode"] = "vision"
    try:  # CU_SETTINGS='{"vision_model": "..."}' overrides for one process (benchmarks, tests); never saved
        merged.update(json.loads(os.environ.get("CU_SETTINGS") or "{}"))
    except ValueError:
        pass
    return merged


def save_settings(s: dict):
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "settings.json").write_text(json.dumps(s, indent=2))


def get_key(name: str) -> str:
    # A process the Keychain doesn't know (e.g. an editor starting the MCP server) gets an
    # "allow access" prompt; without a timeout an unanswered prompt hangs startup forever.
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", name, "-w"],
                           capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def set_key(name: str, value: str):
    if value:
        subprocess.run(["security", "add-generic-password", "-U", "-s", KEYCHAIN_SERVICE, "-a", name, "-w", value],
                       check=True, capture_output=True)
    else:
        subprocess.run(["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", name], capture_output=True)
    os.environ.pop(name, None)
    if value:
        os.environ[name] = value


def load_keys():
    """Environment wins; then a project .env (for development); then the Keychain."""
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    except ImportError:
        pass
    for name in KEYS:
        if not os.environ.get(name):
            value = get_key(name)
            if value:
                os.environ[name] = value
