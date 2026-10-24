"""Local speech-to-text, run entirely on this Mac. Models download the first time they're used.

A model is named "<family>:<source>":
  parakeet:<Hugging Face repo or local folder>   Parakeet on MLX (Apple GPU), e.g. parakeet:mlx-community/parakeet-tdt-0.6b-v3
  moonshine:<tiny|base|repo|local folder>        Moonshine on ONNX, e.g. moonshine:base

LocalListener mirrors voice.Listener: start() opens the mic, `events` gets ("partial", text) while you
talk and ("final", text) after a pause, tick() is a no-op kept for the same interface."""

import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from .voicepack import label, parse

RATE = 16_000
BLOCK = 512  # 32 ms
END_SILENCE = 0.75  # seconds of quiet that end an utterance
PARTIAL_EVERY = 0.6
MAX_UTTERANCE = 30.0

_models: dict = {}
_load_lock = threading.Lock()
# MLX keeps per-thread GPU streams, so every load and transcription runs on this one thread.
_inference = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stt")


def _moonshine_dir(source: str) -> str | None:
    """Folder holding encoder_model.onnx + decoder_model_merged.onnx, for a local path or a Hugging Face repo."""
    path = Path(source).expanduser()
    if not path.exists():
        from huggingface_hub import snapshot_download
        path = Path(snapshot_download(source, allow_patterns=["*.onnx", "*.json"]))
    found = next(path.rglob("encoder_model.onnx"), None)
    if found is None or not (found.parent / "decoder_model_merged.onnx").exists():
        raise ValueError(f"{source} has no encoder_model.onnx / decoder_model_merged.onnx")
    return str(found.parent)


def _load(model: str):
    return _inference.submit(_load_now, model).result()


def _load_now(model: str):
    with _load_lock:
        if model not in _models:
            # Keep one model resident: Parakeet alone is ~2.5 GB, so switching must free the last one.
            if _models:
                import gc
                _models.clear()
                gc.collect()
            family, source = parse(model)
            if family == "parakeet":
                from .melshim import install
                install()  # the packaged helper ships without librosa
                from parakeet_mlx import from_pretrained
                _models[model] = from_pretrained(str(Path(source).expanduser()) if source.startswith(("/", "~")) else source)
            elif family == "moonshine":
                from moonshine_onnx import MoonshineOnnxModel, load_tokenizer
                if source in ("tiny", "base"):
                    onnx = MoonshineOnnxModel(model_name=source)
                else:
                    onnx = MoonshineOnnxModel(models_dir=_moonshine_dir(source))
                _models[model] = (onnx, load_tokenizer())
            else:
                raise ValueError(f"unsupported model {model!r}")
        return _models[model]


def transcribe(model: str, audio: np.ndarray) -> str:
    """audio: mono float32 at 16 kHz."""
    if len(audio) < RATE * 0.15:
        return ""
    return _inference.submit(_transcribe_now, model, audio).result()


def _transcribe_now(model: str, audio: np.ndarray) -> str:
    loaded = _load_now(model)
    if parse(model)[0] == "parakeet":
        import mlx.core as mx
        from parakeet_mlx.audio import get_logmel
        mel = get_logmel(mx.array(audio.astype(np.float32)), loaded.preprocessor_config)  # must be float32
        return loaded.generate(mel)[0].text.strip()
    onnx, tok = loaded
    return tok.decode_batch(onnx.generate(audio[None, :].astype(np.float32)))[0].strip()


def is_downloaded(model: str) -> bool:
    family, source = parse(model)
    if source.startswith(("/", "~")):
        return True
    repo = "UsefulSensors/moonshine" if source in ("tiny", "base") else source
    try:
        from huggingface_hub import scan_cache_dir
        return any(r.repo_id == repo for r in scan_cache_dir().repos)
    except Exception:
        return False


class LocalListener:
    def __init__(self, engine: str):
        self.engine = engine
        self.events: queue.Queue = queue.Queue()
        self.listening = False
        self._audio: queue.Queue = queue.Queue()
        self._stream = None
        self._worker = None
        self._end = False

    def start(self):
        if self.listening:
            return
        import sounddevice as sd

        self._audio = queue.Queue()
        self._end = False
        try:
            self._stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", blocksize=BLOCK,
                                          callback=lambda data, frames, t, status: self._audio.put(data[:, 0].copy()))
            self._stream.start()
        except Exception as e:
            self.events.put(("error", f"Microphone unavailable: {e}"))
            return
        self.listening = True
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def _run(self):
        if self.engine not in _models:
            if not is_downloaded(self.engine):
                self.events.put(("status", f"Downloading {label(self.engine)}…"))
            try:
                _load(self.engine)
            except Exception as e:
                self.events.put(("error", f"Couldn't load speech model: {e}"))
                self._close()
                return
        noise, speech, voiced = 0.01, [], False
        quiet_since, last_partial, started = None, 0.0, None
        while self.listening:
            try:
                block = self._audio.get(timeout=0.2)
            except queue.Empty:
                if self._end:
                    break
                continue
            level = float(np.sqrt(np.mean(block ** 2)))
            loud = level > max(0.012, noise * 3.0)
            if not voiced:
                noise = 0.95 * noise + 0.05 * level  # track the room's background level
                speech = (speech + [block])[-8:]  # keep ~250 ms of lead-in so first words aren't clipped
                if loud:
                    voiced, started, quiet_since = True, time.monotonic(), None
                continue
            speech.append(block)
            now = time.monotonic()
            quiet_since = None if loud else (quiet_since or now)
            if now - last_partial > PARTIAL_EVERY:
                last_partial = now
                text = transcribe(self.engine, np.concatenate(speech))
                if text:
                    self.events.put(("partial", text))
            if self._end or (quiet_since and now - quiet_since > END_SILENCE) or now - started > MAX_UTTERANCE:
                text = transcribe(self.engine, np.concatenate(speech))
                self._close()
                self.events.put(("final", text))
                return
        if self._end and self.listening:  # Talk pressed again before any speech
            self._close()
            self.events.put(("final", ""))

    def _close(self):
        self.listening = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def tick(self):
        pass

    def _finish(self):
        """End the utterance now (Talk pressed again); what was said so far is still transcribed."""
        self._end = True

    def stop(self):
        self._close()
