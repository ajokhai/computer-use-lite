"""The optional Voice Pack: Parakeet and Moonshine in a separate helper program, so the app itself stays small.

The pack is a zip ("Computer Use Voice Pack <version>.zip") holding a `cu-voice` folder. Installing unzips it
into Application Support. The app talks to the helper over stdin/stdout (see voice_helper.py)."""

import json
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from .config import DATA

# Built-in choices. Any other Parakeet (MLX) or Moonshine (ONNX) model can be added as a custom model.
CATALOG = {
    "apple": "Apple — built in",
    "moonshine:tiny": "Moonshine Tiny — lightest (110 MB)",
    "moonshine:base": "Moonshine Base — light, fast (250 MB)",
    "parakeet:mlx-community/parakeet-tdt-0.6b-v2": "Parakeet v2 — English, very accurate (2.5 GB)",
    "parakeet:mlx-community/parakeet-tdt-0.6b-v3": "Parakeet v3 — 25 languages, most accurate (2.5 GB)",
}
FAMILIES = ("parakeet", "moonshine")
ALIASES = {"parakeet": "parakeet:mlx-community/parakeet-tdt-0.6b-v3", "moonshine": "moonshine:base"}

ADD_MODEL_HELP = """Paste a Hugging Face model name or a folder on this Mac.

Supported:
• Parakeet models converted for MLX, e.g. mlx-community/parakeet-tdt_ctc-0.6b-ja
  (a folder needs config.json + model.safetensors)
• Moonshine ONNX models, e.g. a repo or folder with
  encoder_model.onnx + decoder_model_merged.onnx

Prefix with parakeet: or moonshine: if the name doesn't say which.
The model downloads the first time you use it."""


def parse(model: str) -> tuple[str, str]:
    """'parakeet:repo' -> ('parakeet', 'repo')."""
    model = ALIASES.get(model, model)
    family, _, source = model.partition(":")
    return family, source


def normalize(model: str) -> str:
    return ALIASES.get(model, model)


def label(model: str) -> str:
    model = normalize(model)
    if model in CATALOG:
        return CATALOG[model].split(" — ")[0]
    return parse(model)[1].rstrip("/").split("/")[-1]


def guess_model(text: str) -> str | None:
    """Turn what the user pasted into a model id, or None if the family can't be told."""
    text = text.strip().strip("'\"")
    if not text:
        return None
    family, _, rest = text.partition(":")
    if family in FAMILIES and rest:
        return f"{family}:{rest.strip()}"
    path = Path(text).expanduser()
    if path.exists():
        if next(path.rglob("encoder_model.onnx"), None):
            return f"moonshine:{path}"
        if (path / "config.json").exists() and (path / "model.safetensors").exists():
            return f"parakeet:{path}"
        return None
    low = text.lower()
    for family in FAMILIES:
        if family in low:
            return f"{family}:{text}"
    return None


PACK_DIR = DATA / "VoicePack"
HELPER = PACK_DIR / "cu-voice" / "cu-voice"


def _dev_available() -> bool:
    """Running from source with the speech packages installed: no pack needed."""
    if getattr(sys, "frozen", False):
        return False
    try:
        import importlib.util
        return all(importlib.util.find_spec(m) for m in ("parakeet_mlx", "moonshine_onnx", "sounddevice"))
    except Exception:
        return False


def installed() -> bool:
    return HELPER.exists() or _dev_available()


def helper_command(model: str, tts: bool = False) -> list[str]:
    args = ["--tts"] if tts else ["--model", model]
    if HELPER.exists():
        return [str(HELPER), *args]
    return [sys.executable, "-m", "cu.voice_helper", *args]


def project_cwd() -> str | None:
    """From source, run next to the `cu` package so `-m cu.voice_helper` resolves."""
    return None if HELPER.exists() else str(Path(__file__).resolve().parent.parent)


def find_pack() -> Path | None:
    """Look for a downloaded pack zip in the usual places, newest first."""
    places = [Path.home() / "Downloads", Path.home() / "Desktop", *Path("/Volumes").glob("Computer Use*")]
    bundle = Path(sys.executable).resolve()
    places += [p for p in bundle.parents[:5]]
    found = []
    for place in places:
        try:
            found += place.glob("Computer Use Voice Pack*.zip")
        except OSError:
            pass
    return max(found, key=lambda p: p.stat().st_mtime, default=None)


def install(zip_path: Path):
    """Unzip the pack into Application Support, replacing any older one."""
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["ditto", "-x", "-k", str(zip_path), tmp], check=True)
        src = next(Path(tmp).rglob("cu-voice/cu-voice"), None)
        if src is None:
            raise RuntimeError("That zip isn't a Computer Use Voice Pack")
        if PACK_DIR.exists():
            shutil.rmtree(PACK_DIR)
        PACK_DIR.mkdir(parents=True)
        shutil.move(str(src.parent), PACK_DIR / "cu-voice")
    # A pack downloaded from the web is quarantined; the user chose to install it, so clear that.
    subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(PACK_DIR)], capture_output=True)


def uninstall():
    shutil.rmtree(PACK_DIR, ignore_errors=True)


class PackListener:
    """Same interface as voice.Listener, backed by the helper process. The model stays loaded between utterances."""

    def __init__(self, engine: str):
        self.engine = engine
        self.events: queue.Queue = queue.Queue()
        self.listening = False
        self._proc = None

    def _ensure(self):
        if self._proc is not None and self._proc.poll() is None:
            return
        cwd = project_cwd()
        self._proc = subprocess.Popen(helper_command(self.engine), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL, text=True, bufsize=1, cwd=cwd)
        threading.Thread(target=self._read, args=(self._proc,), daemon=True).start()

    def _read(self, proc):
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            kind, text = msg.get("kind"), msg.get("text", "")
            if kind in ("final", "error"):
                self.listening = False
            if kind != "ready":
                self.events.put((kind, text))
        if self.listening:
            self.listening = False
            self.events.put(("error", "Voice Pack helper stopped"))

    def _send(self, cmd: str):
        try:
            self._proc.stdin.write(cmd + "\n")
            self._proc.stdin.flush()
        except (OSError, AttributeError, ValueError):
            pass

    def start(self):
        if self.listening:
            return
        self._ensure()
        self.listening = True
        self._send("listen")

    def tick(self):
        pass

    def _finish(self):
        self._send("end")

    def stop(self):
        self.listening = False
        self._send("stop")

    def close(self):
        self._send("quit")
        self._proc = None
