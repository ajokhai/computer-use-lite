"""App data, settings, and API keys (kept in the macOS Keychain, never in the app bundle)."""

import json
import os
import subprocess
from pathlib import Path

APP_NAME = "Computer Use"
DATA = Path.home() / "Library" / "Application Support" / APP_NAME
LOG = Path.home() / "Library" / "Logs" / "computer-use.log"
KEYCHAIN_SERVICE = "computer-use-lite"
KEYS = ("GEMINI_API_KEY", "TYPESAFE_API_KEY")

DEFAULTS = {
    "mode": "auto",
    "awake": True,
    "confirm_all": False,
    "allow_risky": False,
    "auto_switch": True,
    "auto_update": True,  # install new releases when idle
    "last_version": "",
    "hands_free": False,  # always listening for "Computer, …"
    "speak_replies": True,
    "narrate": False,  # talk through each step while working
    "speaking_voice": "mac:",  # "mac:<name>" (empty = system voice), "kokoro:<id>", or "gemini:<name>" (see cu/say.py)
    "speech_engine": "apple",  # "apple" or a model id like "moonshine:base" (see cu/stt.py)
    "custom_speech_models": [],  # model ids added with Voice Engine › Add Custom Model…
    "gemini_models": ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3-flash-preview"],
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
    return merged


def save_settings(s: dict):
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "settings.json").write_text(json.dumps(s, indent=2))


def get_key(name: str) -> str:
    r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", name, "-w"],
                       capture_output=True, text=True)
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
