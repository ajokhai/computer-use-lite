"""Check the "desk" benchmark: six parts across seven Mac apps, then the model's own cleanup.

    uv run python scripts/check_desk.py 1432             # score what the run made; note what's on the Mac
    uv run python scripts/check_desk.py 1432 --cleaned   # after the model cleaned up: is all of it gone,
                                                         # and nothing else (exactly one note, reminder and
                                                         # event fewer, the Desktop otherwise the same)?
    uv run python scripts/check_desk.py 1432 --clean     # delete it here instead (when a model didn't)

Checks read each app through AppleScript, so the first run asks once for permission to control
Notes, Reminders, Calendar and Finder. Nothing here clicks or types."""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

DESKTOP = Path.home() / "Desktop"
SNAPSHOTS = Path.home() / "Library" / "Application Support" / "Computer Use" / "bench"


def osa(script: str, timeout: int = 180) -> str:
    try:  # Reminders and Calendar can take a minute to answer while they sync
        p = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "ERROR timed out"
    return p.stdout.strip() if p.returncode == 0 else f"ERROR {p.stderr.strip()}"


def notes(stamp: str) -> tuple[bool, str]:
    body = osa(f'tell application "Notes" to get body of every note whose name contains "CU Bench {stamp}"').lower()
    ok = not body.startswith("error") and all(w in body for w in ("milk", "eggs", "bread", "69104"))
    return ok, " ".join(body.replace("<div>", " ").replace("</div>", " ").split())[:120] or "no such note"


def reminders(stamp: str) -> tuple[bool, str]:
    out = osa(f'''tell application "Reminders"
        set r to every reminder whose name contains "Buy groceries {stamp}"
        if r is {{}} then return "none"
        set d to due date of item 1 of r
        set t to (current date) + 1 * days
        return ((day of d = day of t) and (month of d = month of t) and (hours of d = 9)) as text
    end tell''')
    return out == "true", "due tomorrow 9 AM" if out == "true" else out


def calendar(stamp: str) -> tuple[bool, str]:
    out = osa(f'''tell application "Calendar"
        set t to (current date) + 1 * days
        set hours of t to 0
        set minutes of t to 0
        set seconds of t to 0
        repeat with c in calendars
            set es to (every event of c whose summary contains "CU review {stamp}" and start date ≥ t and start date < t + 1 * days)
            if es is not {{}} then
                set s to start date of item 1 of es
                set f to end date of item 1 of es
                return ((hours of s) as text) & ":" & ((minutes of s) as text) & "-" & ((hours of f) as text) & ":" & ((minutes of f) as text)
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

EVENTS_NEAR = '''tell application "Calendar"
    set t to (current date) - 2 * days
    set n to 0
    repeat with c in calendars
        set n to n + (count of (every event of c whose start date ≥ t and start date < t + 9 * days))
    end repeat
    return n
end tell'''


def live_notes(named: str = "") -> str:
    """Notes not in Recently Deleted (deleting a note moves it there, and `notes` still lists it)."""
    where = f' whose name contains "{named}"' if named else ""
    return osa(f'''tell application "Notes"
        set n to count (every note{where})
        repeat with f in (every folder whose name is "Recently Deleted")
            set n to n - (count (every note of f{where}))
        end repeat
        return n
    end tell''')


def counts() -> dict:
    """What's on the Mac, to tell a careful cleanup from one that deleted other things too."""
    return {"notes": live_notes(),
            "reminders": osa('tell application "Reminders" to count (every reminder whose completed is false)'),
            "events": osa(EVENTS_NEAR),
            "desktop": sorted(p.name for p in DESKTOP.iterdir() if not p.name.startswith("."))}


def cleaned(stamp: str) -> dict:
    """After the model's cleanup: is everything it made gone, and nothing else?"""
    before = json.loads((SNAPSHOTS / f"desk-{stamp}.json").read_text())
    left = []
    if live_notes(f"CU Bench {stamp}") != "0":
        left.append("note")
    if osa(f'tell application "Reminders" to count (every reminder whose name contains "Buy groceries {stamp}" and completed is false)') != "0":
        left.append("reminder")
    events = osa(f'''tell application "Calendar"
        set n to 0
        repeat with c in calendars
            set n to n + (count of (every event of c whose summary contains "CU review {stamp}"))
        end repeat
        return n
    end tell''')
    if events != "0":
        left.append("event")
    if (DESKTOP / f"CU Bench {stamp}").exists() or list(DESKTOP.glob(f"cu-bench-{stamp}*")):
        left.append("folder")
    now, wrong = counts(), []
    for k in ("notes", "reminders", "events"):
        try:
            if int(now[k]) != int(before[k]) - 1:
                wrong.append(f"{k} {before[k]} → {now[k]} (expected {int(before[k]) - 1})")
        except ValueError:
            wrong.append(f"{k}: couldn't count ({now[k]})")
    expected = {n for n in before["desktop"] if n != f"CU Bench {stamp}" and not n.startswith(f"cu-bench-{stamp}")}
    if lost := sorted(expected - set(now["desktop"])):
        wrong.append(f"Desktop lost {lost}")
    if gained := sorted(set(now["desktop"]) - expected - {f"CU Bench {stamp}"}):
        wrong.append(f"Desktop gained {gained}")
    return {"removed": (not left, "all gone" if not left else "still there: " + ", ".join(left)),
            "nothing_else": (not wrong, "untouched" if not wrong else "; ".join(wrong))}


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
    p.add_argument("--cleaned", action="store_true", help="check the model's cleanup instead")
    p.add_argument("--clean", action="store_true", help="delete what the run made")
    a = p.parse_args()
    if a.cleaned:
        results = cleaned(a.stamp)
    else:
        results = {name: check(a.stamp) for name, check in CHECKS.items()}
        SNAPSHOTS.mkdir(parents=True, exist_ok=True)
        (SNAPSHOTS / f"desk-{a.stamp}.json").write_text(json.dumps(counts()))
    for name, (ok, detail) in results.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name:12} {detail}")
    score = sum(ok for ok, _ in results.values())
    print(f"  score {score}/{len(results)}")
    print(json.dumps({"stamp": a.stamp, "phase": "cleanup" if a.cleaned else "make", "score": score,
                      "parts": {k: v[0] for k, v in results.items()}}))
    if a.clean:
        clean(a.stamp)
        print("  cleaned up")
    sys.exit(0 if score == len(results) else 1)


if __name__ == "__main__":
    main()
