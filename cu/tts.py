"""Kokoro text-to-speech (82M parameters, Apache-2.0), run inside the Voice Pack helper.
The model (92 MB, 8-bit) and voices (28 MB) download to Application Support the first time they're used."""

import urllib.request

from .config import DATA
from .voicepack import KOKORO_VOICES  # noqa: F401  (kept here for callers)

KOKORO_DIR = DATA / "models" / "kokoro"
RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
FILES = ("kokoro-v1.0.int8.onnx", "voices-v1.0.bin")

_kokoro = None


def downloaded() -> bool:
    return all((KOKORO_DIR / f).exists() for f in FILES)


def load(status=lambda msg: None):
    global _kokoro
    if _kokoro is None:
        KOKORO_DIR.mkdir(parents=True, exist_ok=True)
        for name in FILES:
            path = KOKORO_DIR / name
            if not path.exists():
                status("Downloading Kokoro voices…")
                tmp = path.with_suffix(".part")
                urllib.request.urlretrieve(RELEASE + name, tmp)
                tmp.replace(path)
        from kokoro_onnx import Kokoro
        _kokoro = Kokoro(str(KOKORO_DIR / FILES[0]), str(KOKORO_DIR / FILES[1]))
    return _kokoro


def synth(text: str, voice: str = "af_heart", speed: float = 1.05):
    """-> (float32 samples, sample_rate)."""
    lang = "en-gb" if voice.startswith("b") else "en-us"
    return load().create(text, voice=voice, speed=speed, lang=lang)
