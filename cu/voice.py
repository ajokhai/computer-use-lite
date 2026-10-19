"""Voice input with Apple's on-device speech recognition (no API key, audio never leaves the Mac). Spoken output lives in say.py.

Listener streams microphone audio into SFSpeechRecognizer, reports partial transcripts as you speak,
and ends an utterance after a short silence. Recognition callbacks arrive on background threads;
callers get them through `events` and call `tick()` from their main loop."""

import queue
import threading
import time

import AVFoundation
import Speech
from Foundation import NSLocale

SILENCE_SECS = 1.2  # pause that ends an utterance
WAKE_WORDS = ("hey computer", "computer")
STOP_WORDS = {"stop", "cancel", "stop it", "abort", "halt", "never mind", "nevermind"}


def authorize(callback):
    """Ask for Speech Recognition permission (the mic prompt comes when audio starts)."""
    status = Speech.SFSpeechRecognizer.authorizationStatus()
    if status == Speech.SFSpeechRecognizerAuthorizationStatusAuthorized:
        callback(True)
    elif status == Speech.SFSpeechRecognizerAuthorizationStatusNotDetermined:
        Speech.SFSpeechRecognizer.requestAuthorization_(
            lambda s: callback(s == Speech.SFSpeechRecognizerAuthorizationStatusAuthorized))
    else:
        callback(False)


class Listener:
    def __init__(self, locale: str = "en-US"):
        self.recognizer = Speech.SFSpeechRecognizer.alloc().initWithLocale_(NSLocale.localeWithLocaleIdentifier_(locale))
        self.engine = AVFoundation.AVAudioEngine.alloc().init()
        self.events: queue.Queue = queue.Queue()  # ("partial", text) | ("final", text) | ("error", msg)
        self.listening = False
        self._request = None
        self._task = None
        self._text = ""
        self._changed = 0.0
        self._lock = threading.Lock()

    def start(self):
        if self.listening:
            return
        if not self.recognizer.isAvailable():
            self.events.put(("error", "Speech recognition isn't available right now"))
            return
        req = Speech.SFSpeechAudioBufferRecognitionRequest.alloc().init()
        req.setShouldReportPartialResults_(True)
        if self.recognizer.supportsOnDeviceRecognition():
            req.setRequiresOnDeviceRecognition_(True)  # private and works offline
        node = self.engine.inputNode()
        fmt = node.outputFormatForBus_(0)
        node.removeTapOnBus_(0)
        node.installTapOnBus_bufferSize_format_block_(0, 1024, fmt, lambda buf, when: req.appendAudioPCMBuffer_(buf))
        self.engine.prepare()
        ok, err = self.engine.startAndReturnError_(None)
        if not ok:
            node.removeTapOnBus_(0)
            self.events.put(("error", f"Microphone unavailable: {err}"))
            return
        with self._lock:
            self._request, self._text, self._changed = req, "", time.monotonic()
        self._task = self.recognizer.recognitionTaskWithRequest_resultHandler_(req, self._on_result)
        self.listening = True

    def _on_result(self, result, error):
        if result is not None:
            text = str(result.bestTranscription().formattedString())
            with self._lock:
                if text != self._text:
                    self._text, self._changed = text, time.monotonic()
            self.events.put(("partial", text))
            if result.isFinal():
                self._finish()
        elif error is not None and self.listening:
            # "No speech detected" and cancellations are normal; anything else is reported.
            msg = str(error.localizedDescription())
            if "no speech" not in msg.lower() and error.code() not in (216, 301, 1110):
                self.events.put(("error", msg))
            self._finish(emit=False)

    def tick(self):
        """Call regularly from the main loop: ends the utterance after a pause."""
        with self._lock:
            done = self.listening and self._text and time.monotonic() - self._changed > SILENCE_SECS
        if done:
            self._finish()

    def _finish(self, emit: bool = True):
        with self._lock:
            if not self.listening:
                return
            self.listening = False
            text, req = self._text.strip(), self._request
        self.engine.stop()
        self.engine.inputNode().removeTapOnBus_(0)
        if req is not None:
            req.endAudio()
        if self._task is not None:
            self._task.cancel()
            self._task = None
        if emit:
            self.events.put(("final", text))

    def stop(self):
        self._finish(emit=False)


def strip_wake_word(text: str) -> str | None:
    """'Computer, open Notes' -> 'open Notes'; None if no wake word."""
    low = text.lower()
    for w in WAKE_WORDS:
        i = low.find(w)
        if i != -1:
            return text[i + len(w):].lstrip(" ,.:!?-")
    return None


def is_stop(text: str) -> bool:
    words = text.lower().strip(" .!?,")
    return words in STOP_WORDS or any(words.endswith(" " + s) for s in ("stop", "cancel"))


