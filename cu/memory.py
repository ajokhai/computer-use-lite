"""What the agent has learned: short lessons about an app or site (what went wrong, why, and what
worked instead), kept across runs and shared by every model and the MCP server.

Stored in lessons.json next to the settings, as {"version": 1, "lessons": [lesson, ...]}, where a
lesson is {"where", "task", "what", "fix", "outcome", "model", "at", "seen", "used"}:
  where    lower-case app name or site ("google chrome", "x.com")
  task     the task it came from, shortened
  what     what happened ("clicked the 'What's happening' heading; typing went to the page")
  fix      what worked, or what to try next time ("click the 'Post text' box first")
  outcome  "worked" (the fix was confirmed) or "failed" (only what to avoid is known)
  seen     times the same lesson was recorded; used: times it was handed to a model
Bounded: at most MAX_LESSONS lessons of MAX_TEXT characters per field; the least useful go first."""

import fcntl
import json
import re
import time
from contextlib import contextmanager

from .config import DATA

FILE = DATA / "lessons.json"
MAX_LESSONS = 200
MAX_TEXT = 300
DOMAIN = re.compile(r"\b((?:[a-z0-9-]+\.)+(?:com|org|net|io|ai|app|dev|co|gov|edu|me|tv|uk|ng))\b", re.I)


def _clip(text, n: int = MAX_TEXT) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def places(task: str = "", app: str = "") -> list[str]:
    """The keys a task touches: the frontmost app and any sites named in the task."""
    keys = [app.strip().lower()] if app and app.strip() else []
    for d in DOMAIN.findall(task or ""):
        d = d.lower().removeprefix("www.")
        if d not in keys and "github.com" != d:  # links to share aren't places to work in
            keys.append(d)
    return keys


@contextmanager
def _locked():
    DATA.mkdir(parents=True, exist_ok=True)
    with open(DATA / "lessons.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            data = json.loads(FILE.read_text())
            if not isinstance(data.get("lessons"), list):
                raise ValueError
        except (OSError, ValueError, AttributeError):
            data = {"version": 1, "lessons": []}
        yield data
        FILE.write_text(json.dumps(data, indent=1))


def _score(lesson: dict) -> float:
    """How much a lesson is worth keeping: confirmed fixes, repeats and recent use count."""
    age_days = (time.time() - lesson.get("at", 0)) / 86400
    return (2.0 if lesson.get("outcome") == "worked" else 1.0) + lesson.get("seen", 1) * 0.5 \
        + lesson.get("used", 0) * 0.2 - age_days / 30


def record(where: str, task: str, what: str, fix: str, outcome: str = "failed", model: str = "") -> dict:
    """Save a lesson, merging it with the same lesson for the same place."""
    lesson = {"where": _clip(where, 60).lower(), "task": _clip(task, 120), "what": _clip(what), "fix": _clip(fix),
              "outcome": "worked" if outcome == "worked" else "failed", "model": _clip(model, 40),
              "at": time.time(), "seen": 1, "used": 0}
    with _locked() as data:
        for old in data["lessons"]:
            if old.get("where") == lesson["where"] and old.get("fix", "").lower() == lesson["fix"].lower():
                old.update(what=lesson["what"], at=lesson["at"], model=lesson["model"] or old.get("model", ""),
                           seen=old.get("seen", 1) + 1)
                if lesson["outcome"] == "worked":
                    old["outcome"] = "worked"
                return dict(old)
        data["lessons"].append(lesson)
        if len(data["lessons"]) > MAX_LESSONS:
            data["lessons"].sort(key=_score, reverse=True)
            del data["lessons"][MAX_LESSONS:]
    return lesson


def recall(keys: list[str], task: str = "", limit: int = 6) -> list[dict]:
    """The most relevant lessons for these places (and this task), best first; marks them used."""
    keys = [k.lower() for k in keys if k]
    if not keys:
        return []
    words = set(re.findall(r"[a-z]{4,}", (task or "").lower()))
    with _locked() as data:
        hits = []
        for i, l in enumerate(data["lessons"]):
            if l.get("where") not in keys:
                continue
            overlap = len(words & set(re.findall(r"[a-z]{4,}", l.get("task", "").lower())))
            hits.append((overlap + _score(l), i))
        hits.sort(reverse=True)
        chosen = [i for _, i in hits[:limit]]
        for i in chosen:
            data["lessons"][i]["used"] = data["lessons"][i].get("used", 0) + 1
        return [dict(data["lessons"][i]) for i in chosen]


def as_text(lessons: list[dict]) -> str:
    """Lessons as short lines a model (or a person) can read."""
    lines = []
    for l in lessons:
        tag = "worked" if l.get("outcome") == "worked" else "not confirmed yet"
        lines.append(f"- On {l['where']}: {l['what']} → {l['fix']} ({tag})")
    return "\n".join(lines)


def all_lessons() -> list[dict]:
    with _locked() as data:
        return [dict(l) for l in data["lessons"]]


def forget(where: str | None = None):
    """Remove every lesson, or every lesson for one place."""
    with _locked() as data:
        data["lessons"] = [] if where is None else [l for l in data["lessons"] if l.get("where") != where.lower()]


# --- sharing: export and import, with personal details removed ----------------------------

EXPORT_FORMAT = "computer-use-lessons"
_SCRUB = [
    (re.compile(r"\b(sk-[A-Za-z0-9_-]{8,}|AIza[0-9A-Za-z_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[abp]-[A-Za-z0-9-]+)"), "<key>"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "<email>"),
    (re.compile(r"(?<![\w/])@[A-Za-z0-9_]{2,30}\b"), "@<user>"),
    (re.compile(r"https?://([^/\s]+)[^\s]*"), r"https://\1"),  # keep the site, drop paths and queries
    (re.compile(r"/Users/[^/\s]+"), "/Users/<user>"),
    (re.compile(r"\+?\d[\d\s().-]{7,}\d"), "<number>"),
    (re.compile(r"\b\d{5,}\b"), "<number>"),
    (re.compile(r"[\"“”']([^\"“”']{3,})[\"“”']"), "\"<text>\""),  # quoted text is usually the user's own content
]


def sanitize(text: str, names: list[str] | None = None) -> str:
    """Remove what could identify a person or account: keys, emails, @handles, URL paths, home
    folders, phone and account numbers, quoted content, and any given names."""
    out = str(text or "")
    for pattern, repl in _SCRUB:
        out = pattern.sub(repl, out)
    for n in names or []:
        if n and len(n) > 1:
            out = re.sub(re.escape(n), "<name>", out, flags=re.I)
    return _clip(out)


def export(path, names: list[str] | None = None) -> int:
    """Write every lesson, sanitized, to a file another user can import; returns how many.
    Kept: where, what, fix, outcome, seen. Dropped: the original task, the model, timestamps, use counts."""
    import getpass
    import pwd
    me = [getpass.getuser()]
    try:
        me += [p for p in pwd.getpwnam(me[0]).pw_gecos.split() if len(p) > 1]
    except KeyError:
        pass
    names = [*(names or []), *me]
    lessons = [{"where": l["where"], "what": sanitize(l.get("what"), names), "fix": sanitize(l.get("fix"), names),
                "outcome": l.get("outcome", "failed"), "seen": l.get("seen", 1)} for l in all_lessons()]
    with open(path, "w") as f:
        json.dump({"format": EXPORT_FORMAT, "version": 1, "lessons": lessons}, f, indent=1)
    return len(lessons)


def import_(path) -> int:
    """Add lessons from an exported file (sanitized again on the way in); returns how many were new or merged."""
    with open(path) as f:
        data = json.load(f)
    if data.get("format") != EXPORT_FORMAT or not isinstance(data.get("lessons"), list):
        raise ValueError("not a Computer Use lessons file")
    n = 0
    for l in data["lessons"][:MAX_LESSONS]:
        if not isinstance(l, dict) or not l.get("where") or not l.get("fix"):
            continue
        record(str(l["where"]), "", sanitize(l.get("what")), sanitize(l.get("fix")),
               "worked" if l.get("outcome") == "worked" else "failed", model="imported")
        n += 1
    return n
