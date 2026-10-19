"""The Voice Pack's helper process: local speech-to-text, driven over stdin/stdout.

stdin commands, one per line:  listen | end | stop | quit
stdout events, JSON per line:  {"kind": "ready" | "status" | "partial" | "final" | "error", "text": "..."}

With --tts it speaks instead (Kokoro): stdin lines are {"text": ..., "voice": ...} JSON, or stop | quit;
events are "speaking" when audio starts and "done" when it ends.

Run: cu-voice --model parakeet:mlx-community/parakeet-tdt-0.6b-v3   (or: python -m cu.voice_helper --model ...)"""

import argparse
import json
import sys
import threading
import time


def emit(kind: str, text: str = ""):
    sys.stdout.write(json.dumps({"kind": kind, "text": text}) + "\n")
    sys.stdout.flush()


def main():
    p = argparse.ArgumentParser(prog="cu-voice")
    p.add_argument("--model", default="parakeet", help="model id, e.g. moonshine:base (see cu/stt.py)")
    p.add_argument("--tts", action="store_true", help="speak with Kokoro instead of listening")
    args = p.parse_args()
    if args.tts:
        return speak_loop()
    from cu.voicepack import normalize
    engine = normalize(args.model)

    from cu.stt import LocalListener, _load

    listener = LocalListener(engine)
    threading.Thread(target=_load, args=(engine,), daemon=True).start()  # warm up before the first "listen"

    def forward():
        while True:
            kind, text = listener.events.get()
            emit(kind, text)

    threading.Thread(target=forward, daemon=True).start()
    emit("ready")
    for line in sys.stdin:
        cmd = line.strip()
        if cmd == "listen":
            listener.start()
        elif cmd == "end":
            listener._finish()
        elif cmd == "stop":
            listener.stop()
        elif cmd == "quit":
            break
    listener.stop()
    time.sleep(0.1)


def speak_loop():
    import queue

    import sounddevice as sd

    from cu import tts

    jobs: queue.Queue = queue.Queue()

    def worker():
        try:
            tts.load(status=lambda msg: emit("status", msg))
        except Exception as e:
            emit("error", f"Couldn't load Kokoro: {e}")
        while True:
            job = jobs.get()
            if job is None:
                return
            try:
                samples, rate = tts.synth(job["text"], job.get("voice") or "af_heart")
                if jobs.empty():  # a newer line replaced this one while synthesizing
                    emit("speaking")
                    sd.play(samples, rate)
                    sd.wait()
            except Exception as e:
                emit("error", str(e))
            emit("done")

    threading.Thread(target=worker, daemon=True).start()
    emit("ready")
    for line in sys.stdin:
        line = line.strip()
        if line in ("stop", "quit"):
            sd.stop()
            while not jobs.empty():
                jobs.get_nowait()
            if line == "quit":
                break
        elif line:
            try:
                sd.stop()
                jobs.put(json.loads(line))
            except ValueError:
                pass
    jobs.put(None)


if __name__ == "__main__":
    main()
