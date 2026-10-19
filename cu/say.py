"""Spoken output, shared by the menu bar app and the MCP server. Remembers what it said recently so the
microphone can ignore its own voice.

A voice is "<source>:<name>":
  mac:<name>      a voice installed on this Mac (the `say` command); the default
  kokoro:<id>     Kokoro, local neural voices from the Voice Pack, e.g. kokoro:af_heart
  gemini:<name>   Gemini's voices (needs a Gemini API key), e.g. gemini:Kore
"""

import base64
import difflib
import json
import os
import re
import subprocess
import tempfile
import threading
import time

NOVELTY = {"Albert", "Bad News", "Bahh", "Bells", "Boing", "Bubbles", "Cellos", "Good News", "Jester", "Organ",
           "Superstar", "Trinoids", "Whisper", "Wobble", "Zarvox", "Fred", "Junior", "Ralph", "Kathy"}

GEMINI_TTS_MODEL = os.environ.get("GEMINI_TTS_MODEL", "gemini-3.8-flash-lite-tts")  # the faster TTS model
GEMINI_VOICES = {
    "Zephyr": "bright", "Puck": "upbeat", "Charon": "informative", "Kore": "firm", "Fenrir": "excitable",
    "Leda": "youthful", "Orus": "firm", "Aoede": "breezy", "Callirrhoe": "easy-going", "Autonoe": "bright",
    "Enceladus": "breathy", "Iapetus": "clear", "Umbriel": "easy-going", "Algieba": "smooth", "Despina": "smooth",
    "Erinome": "clear", "Algenib": "gravelly", "Rasalgethi": "informative", "Laomedeia": "upbeat",
    "Achernar": "soft", "Alnilam": "firm", "Schedar": "even", "Gacrux": "mature", "Pulcherrima": "forward",
    "Achird": "friendly", "Zubenelgenubi": "casual", "Vindemiatrix": "gentle", "Sadachbia": "lively",
    "Sadaltager": "knowledgeable", "Sulafat": "warm",
}

_proc: subprocess.Popen | None = None  # `say` or `afplay`
_kokoro = None
_busy_until = 0.0  # Gemini: request in flight
_generation = 0
_recent: list[tuple[float, str]] = []
_lock = threading.Lock()


def mac_voices(language: str = "en") -> list[tuple[str, str]]:
    """Installed Mac voices for a language as (name, locale), best-sounding first."""
    out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
    found = []
    for line in out.splitlines():
        m = re.match(r"^(.+?)\s+([a-z]{2,3}_[A-Z0-9]+)\s+#", line)
        if m and m.group(2).startswith(language) and m.group(1).split(" (")[0] not in NOVELTY:
            found.append((m.group(1).strip(), m.group(2)))
    rank = lambda v: (0 if "Premium" in v[0] else 1 if "Enhanced" in v[0] else 2, v[0])
    return sorted(found, key=rank)


def speaking() -> bool:
    if _proc is not None and _proc.poll() is None:
        return True
    if _kokoro is not None and _kokoro.busy:
        return True
    return time.time() < _busy_until


def stop():
    global _generation, _busy_until
    _generation += 1
    _busy_until = 0.0
    if _proc is not None and _proc.poll() is None:
        _proc.terminate()
    if _kokoro is not None:
        _kokoro.stop()


def speak(text: str, voice: str | None = None, interrupt: bool = True):
    """Say text. interrupt=False drops it when something is already being said (for narration)."""
    text = " ".join(str(text).split())[:300]
    if not text:
        return
    if speaking():
        if not interrupt:
            return
        stop()
    now = time.time()
    with _lock:
        _recent[:] = [(t, s) for t, s in _recent if now - t < 20] + [(now, text.lower())]
    source, _, name = (voice or "mac:").partition(":")
    try:
        if source == "kokoro":
            return _speak_kokoro(text, name or "af_heart")
        if source == "gemini" and (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
            return _speak_gemini(text, name or "Kore")
    except Exception:
        pass  # fall back to the Mac voice rather than staying silent
    _speak_mac(text, name if source == "mac" else "")


def _speak_mac(text: str, name: str = ""):
    global _proc
    cmd = ["say", "-r", "200"] + (["-v", name] if name else [])
    _proc = subprocess.Popen(cmd + [text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _speak_gemini(text: str, name: str):
    global _busy_until
    gen = _generation
    _busy_until = time.time() + 15

    def run():
        global _proc, _busy_until
        try:
            from google import genai
            client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
            it = client.interactions.create(
                model=GEMINI_TTS_MODEL,
                input=[{"type": "user_input", "content": [{"type": "text", "text": text}]}],
                response_format={"type": "audio"},
                generation_config={"speech_config": [{"voice": name}]},
            )
            from . import usage
            usage.record(GEMINI_TTS_MODEL)
            if gen != _generation:
                return  # interrupted while waiting
            wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            wav.write(base64.b64decode(it.output_audio.data))
            wav.close()
            _proc = subprocess.Popen(["afplay", wav.name])
        except Exception:
            if gen == _generation:
                _speak_mac(text)
        finally:
            _busy_until = 0.0

    threading.Thread(target=run, daemon=True).start()


class _KokoroSpeaker:
    """The Voice Pack helper in --tts mode, kept running so each line starts fast."""

    def __init__(self):
        from .voicepack import helper_command, project_cwd
        self.busy = False
        self.proc = subprocess.Popen(helper_command("", tts=True), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, bufsize=1, cwd=project_cwd())
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            try:
                kind = json.loads(line).get("kind")
            except ValueError:
                continue
            if kind in ("done", "error"):
                self.busy = False

    def say(self, text: str, voice: str):
        self.busy = True
        self.proc.stdin.write(json.dumps({"text": text, "voice": voice}) + "\n")
        self.proc.stdin.flush()

    def stop(self):
        self.busy = False
        try:
            self.proc.stdin.write("stop\n")
            self.proc.stdin.flush()
        except (OSError, ValueError):
            pass

    def alive(self) -> bool:
        return self.proc.poll() is None


def _speak_kokoro(text: str, voice: str):
    global _kokoro
    from .voicepack import installed
    if not installed():
        raise RuntimeError("Voice Pack not installed")
    if _kokoro is None or not _kokoro.alive():
        _kokoro = _KokoroSpeaker()
    _kokoro.say(text, voice)


def is_echo(heard: str) -> bool:
    """True when the mic most likely picked up something we just said."""
    heard = heard.lower().strip(" .!?,")
    if not heard:
        return False
    now = time.time()
    with _lock:
        recent = list(_recent)
    for t, said in recent:
        if now - t < 20 and (heard in said or difflib.SequenceMatcher(None, heard, said).ratio() > 0.6):
            return True
    return False
