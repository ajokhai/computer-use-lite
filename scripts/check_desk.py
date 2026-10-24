"""Check (and clean up) the "desk" benchmark: six parts across seven Mac apps, scored separately.

    uv run python scripts/check_desk.py 1432            # score the run with stamp 1432
    uv run python scripts/check_desk.py 1432 --clean    # then delete what it made

Checks read each app through AppleScript, so the first run asks once for permission to control
Notes, Reminders, Calendar and Finder. Nothing here clicks or types."""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

DESKTOP = Path.home() / "Desktop"


def osa(script: str) -> str:
    p = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=60)
    return p.stdout.strip() if p.returncode == 0 else f"ERROR {p.stderr.strip()}"


def notes(stamp: str) -> tuple[bool, str]:
    body = osa(f'tell application "Notes" to get body of every note whose name contains "CU Bench {stamp}"').lower()
    ok = not body.startswith("error") and all(w in body for w in ("milk", "eggs", "bread", "69104"))
    return ok, body[:200] or "no such note"


def reminders(stamp: str) -> tuple[bool, str]:
    out = osa(f'''tell application "Reminders"
        set r to every reminder whose name contains "Buy groceries {stamp}"
        if r is {{}} then return "none"
        set d to due date of item 1 of r
        set t to (current date) + 1 * days
        return ((day of d = day of t) and (month of d = month of t) and (hours of d = 9)) as text
    end tell''')
    return out == "true", out


def calendar(stamp: str) -> tuple[bool, str]:
    out = osa(f'''tell application "Calendar"
        set t to (current date) + 1 * days
        set hours of t to 0
        set minutes of t to 0
        set seconds of t to 0
        repeat with c in calendars
            set es to (every event of c whose summary contains "CU review {stamp}" and start date ≥ t and start date < t + 1 * days)
            if es is not {{}} then
                set e to item 1 of es
                return ((hours of (start date of e)) as text) & ":" & ((minutes of (start date of e)) as text) & "-" & ((hours of (end date of e)) as text) & ":" & ((minutes of (end date of e)) as text)
            end if
        end repeat
        return "none"
    end tell''')
    return out == "15:0-15:30", out


def document(stamp: str) -> tuple[bool, str]:
    folder = DESKTOP / f"CU Bench {stamp}"
    files = sorted(folder.glob(f"cu-bench-{stamp}*")) if folder.is_dir() else []
    return bool(files), ", ".join(f.name for f in files) or ("empty folder" if folder.is_dir() else "no folder")


def finder(stamp: str) -> tuple[bool, str]:
    out = osa('tell application "Finder" to if (count of Finder windows) > 0 then get POSIX path of (target of front Finder window as alias)')
    return out.rstrip("/").endswith(f"CU Bench {stamp}"), out


QUIT = ("Calculator", "TextEdit", "Reminders", "Calendar")


def closed(stamp: str) -> tuple[bool, str]:
    """Checked first: the checks below open Reminders and Calendar themselves."""
    running = [a for a in QUIT if osa(f'application "{a}" is running') == "true"]
    return not running, "still open: " + ", ".join(running) if running else "all quit"


CHECKS = {"closed": closed, "notes": notes, "reminders": reminders, "calendar": calendar, "document": document, "finder": finder}


def clean(stamp: str):
    osa(f'tell application "Notes" to delete (every note whose name contains "CU Bench {stamp}")')
    osa(f'tell application "Reminders" to delete (every reminder whose name contains "Buy groceries {stamp}")')
    osa(f'tell application "Calendar" to repeat with c in calendars\n delete (every event of c whose summary contains "CU review {stamp}")\n end repeat')
    folder = DESKTOP / f"CU Bench {stamp}"
    if folder.is_dir():
        shutil.rmtree(folder)
    for stray in DESKTOP.glob(f"cu-bench-{stamp}*"):  # saved to the Desktop instead of the folder
        stray.unlink()


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("stamp")
    p.add_argument("--clean", action="store_true")
    a = p.parse_args()
    results = {name: check(a.stamp) for name, check in CHECKS.items()}
    for name, (ok, detail) in results.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name:10} {detail}")
    score = sum(ok for ok, _ in results.values())
    print(f"  score {score}/{len(CHECKS)}")
    print(json.dumps({"stamp": a.stamp, "score": score, "parts": {k: v[0] for k, v in results.items()}}))
    if a.clean:
        clean(a.stamp)
        print("  cleaned up")
    sys.exit(0 if score == len(CHECKS) else 1)


if __name__ == "__main__":
    main()
