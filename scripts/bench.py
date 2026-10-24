"""Benchmark the app's own agent on a live task: one run per model, timed, with the log kept.

    uv run python scripts/bench.py jev gemini claude --task tweet

This drives the real Mac (mouse, keyboard, apps) and may act on real accounts, so only run it
when that's what you want. The mouse in a screen corner stops a run. Correctness is checked
separately (each task names its own check); results are appended to bench-results.jsonl."""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent

# name -> (mode, settings override)
MODELS = {
    "jev": ("jev", {}),
    "gemini": ("vision", {"vision_model": "gemini-3.8-flash"}),
    "claude": ("vision", {"vision_model": "claude-opus-5-5"}),
    "claude-sonnet": ("vision", {"vision_model": "claude-sonnet-5-5"}),
    "openai": ("vision", {"vision_model": "gpt-6.1-sol"}),
    "hybrid-gemini": ("hybrid", {"vision_model": "gemini-3.8-flash"}),
    "hybrid-claude": ("hybrid", {"vision_model": "claude-opus-5-5"}),
}
LABELS = {"jev": "Jev", "gemini": "Gemini", "claude": "Claude", "claude-sonnet": "Claude Sonnet",
          "openai": "OpenAI", "hybrid-gemini": "Gemini and Jev", "hybrid-claude": "Claude and Jev"}

TASKS = {
    "tweet": ("In Google Chrome, switch to the profile named \"familymake\", go to x.com, and post a tweet "
              "saying that you, {label}, are testing computer use with this account, and include the link "
              "https://github.com/ajokhai/computer-use-lite. The wording is up to you; for example: "
              "\"This is {label} testing computer use with my account. https://github.com/ajokhai/computer-use-lite\""),
    # Mac apps only, no browser; scored part by part with scripts/check_desk.py {stamp}
    "desk": ("Do this with the apps on this Mac, not in a browser. "
             "1. Open Calculator and work out 1234 × 56. "
             "2. In Notes, make a new note titled \"CU Bench {stamp}\" listing milk, eggs and bread, one per line, "
             "then a last line with the Calculator result. "
             "3. In Reminders, add \"Buy groceries {stamp}\" due tomorrow at 9 AM. "
             "4. In Calendar, add an event \"CU review {stamp}\" tomorrow from 3:00 to 3:30 PM. "
             "5. In TextEdit, write \"Shopping list {stamp}: milk, eggs, bread\" and save it as \"cu-bench-{stamp}\" "
             "in a new folder named \"CU Bench {stamp}\" on the Desktop. "
             "6. Quit Calculator, TextEdit, Reminders and Calendar, and leave that folder open in Finder."),
}
# Tasks that run right after another, in the same stamp, once it has been checked.
FOLLOW_UPS = {
    "desk": ("Clean up after a test, carefully: delete only these and nothing else. "
             "1. In Notes, delete the note titled \"CU Bench {stamp}\". "
             "2. In Reminders, delete \"Buy groceries {stamp}\". "
             "3. In Calendar, delete the event \"CU review {stamp}\" tomorrow. "
             "4. Move the folder \"CU Bench {stamp}\" on the Desktop to the Trash. "
             "Then quit Notes, Reminders and Calendar."),
}
# How a task is checked: a script given the stamp (and --cleaned for a follow-up), exit 0 = all passed.
CHECKERS = {"desk": "scripts/check_desk.py"}


def check(name: str, stamp: str, *flags: str) -> dict:
    p = subprocess.run([sys.executable, CHECKERS[name], stamp, *flags], cwd=PROJECT, capture_output=True, text=True)
    print("\n".join(ln for ln in p.stdout.splitlines() if ln.startswith("  ")), flush=True)
    last = (p.stdout.strip().splitlines() or ["{}"])[-1]
    try:
        return json.loads(last)
    except ValueError:
        return {"error": (p.stderr or p.stdout)[-300:]}


def run(model: str, task: str, timeout: int) -> dict:
    mode, override = MODELS[model]
    cmd = [sys.executable, "-m", "cu", "--mode", mode, "--yolo", "--", task]
    # No switching: a run is about one model, so another one stepping in would make the result a lie.
    env = {**__import__("os").environ, "CU_SETTINGS": json.dumps({"auto_switch": False, **override})}
    t0 = time.monotonic()
    try:
        p = subprocess.run(cmd, cwd=PROJECT, env=env, capture_output=True, text=True, timeout=timeout)
        out, code = (p.stdout + p.stderr), p.returncode
    except subprocess.TimeoutExpired as e:
        out, code = f"{e.stdout or ''}{e.stderr or ''}\nTIMED OUT after {timeout}s", -1
    seconds = round(time.monotonic() - t0, 1)
    lines = [ln for ln in out.splitlines() if ln.strip()]
    switched = [ln for ln in lines if "switching to" in ln or "carries on alone" in ln]
    if switched:  # belt and braces: the result is void if another model acted
        code = code or 2
    return {"model": model, "mode": mode, "task": task, "seconds": seconds, "exit": code,
            "result": next((ln for ln in lines if ln.startswith(("done", "stopped", "blocked"))), lines[-1] if lines else ""),
            "actions": sum(1 for ln in lines if "→" in ln), "log_tail": lines[-40:],
            "void": bool(switched),
            "at": time.strftime("%Y-%m-%d %H:%M:%S")}


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("models", nargs="+", choices=MODELS)
    p.add_argument("--task", default="tweet", choices=TASKS)
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--no-cleanup", action="store_true",
                   help="skip a model's own cleanup and delete the test items by exact name instead (for models "
                        "that can't see which item they'd delete, on a Mac with real data)")
    p.add_argument("--out", default=str(PROJECT.parent / "bench-results.jsonl"))
    a = p.parse_args()
    for m in a.models:
        stamp = time.strftime("%H%M%S")  # unique per run, so a check can't be fooled by leftovers
        task = TASKS[a.task].format(label=LABELS[m], stamp=stamp)
        print(f"▶ {m} [stamp {stamp}]: {task}", flush=True)
        r = run(m, task, a.timeout) | {"stamp": stamp}
        print(f"  {r['seconds']}s, {r['actions']} actions, exit {r['exit']}: {r['result']}", flush=True)
        if a.task in CHECKERS:
            r["check"] = check(a.task, stamp)
        if a.task in FOLLOW_UPS and a.no_cleanup:
            subprocess.run([sys.executable, CHECKERS[a.task], stamp, "--clean"], cwd=PROJECT, capture_output=True)
            r["cleanup"] = {"skipped": "deleted by the checker by exact name"}
        elif a.task in FOLLOW_UPS:
            follow = FOLLOW_UPS[a.task].format(label=LABELS[m], stamp=stamp)
            print(f"▶ {m} cleans up: {follow}", flush=True)
            c = run(m, follow, a.timeout)
            print(f"  {c['seconds']}s, {c['actions']} actions, exit {c['exit']}: {c['result']}", flush=True)
            r["cleanup"] = {k: c[k] for k in ("seconds", "actions", "exit", "result", "void")} | {
                "check": check(a.task, stamp, "--cleaned")}
        with open(a.out, "a") as f:
            f.write(json.dumps(r) + "\n")
        time.sleep(3)  # let the screen settle between runs


if __name__ == "__main__":
    main()
