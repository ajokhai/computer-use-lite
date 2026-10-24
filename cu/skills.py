"""Skills: named, reusable ways of doing something in an app or site ("Post on X", "Make a note in
Notes"), built from what the agent learned, so people can train their Computer Use and share it.

A skill comes from one of four places:
  user      made on purpose: written by the user, or saved from a task that just worked
  learned   made automatically after a task, but only when it's worth keeping (see `learn`)
  video     learned by watching a screen recording of someone doing the task
  imported  from someone else's skill pack

Stored in skills.json next to the settings, as {"version": 1, "skills": [skill, ...]}, where a skill is
{"id", "name", "where", "when", "steps": [str], "notes": [str], "source", "uses", "wins", "fails",
 "created", "updated"}. Steps are generic: the user's own content becomes {text}, {name}, {url}.
Bounded: MAX_SKILLS skills of at most MAX_STEPS steps; learned skills that keep failing, or that
nobody uses, are dropped first; skills the user made are never dropped automatically."""

import fcntl
import io
import json
import re
import time
import uuid
from contextlib import contextmanager
from typing import Callable

from . import memory
from .config import DATA

FILE = DATA / "skills.json"
MAX_SKILLS = 100
MAX_STEPS = 20
STEP_CHARS = 200
PACK_FORMAT = "computer-use-skills"
AUTO_MIN_WINS = 2  # a learned skill needs this many successes of the same kind of task, or one hard-won success

DISTILL_SYSTEM = """You turn a successful computer-use run on macOS into a reusable skill.
You get the task and the log of actions that worked. Reply with JSON only:
{"name": "<3-6 words, Title Case, e.g. Post on X>", "where": "<app name or site, lower case>",
 "when": "<one sentence: which requests this skill is for>",
 "steps": ["<generic step>", ...], "notes": ["<gotcha learned>", ...], "worth_keeping": true}
Steps are short and generic: use the visible labels of buttons and fields, and write the user's own
content as {text}, {name} or {url}; never copy personal details. Leave out failed or wasted actions.
Set worth_keeping false if the task was trivial (one obvious step) or too specific to reuse."""

VIDEO_SYSTEM = """The image is a grid of frames, in reading order, from a screen recording of someone
doing a task on a computer. Work out the task and turn it into a reusable skill. Reply with JSON only:
{"name": "<3-6 words, Title Case>", "where": "<app name or site, lower case>",
 "when": "<one sentence: which requests this skill is for>",
 "steps": ["<generic step, using the visible labels of buttons and fields>", ...],
 "notes": ["<anything easy to get wrong>"], "worth_keeping": true}
Write the person's own content as {text}, {name} or {url}; never copy personal details."""


@contextmanager
def _locked():
    DATA.mkdir(parents=True, exist_ok=True)
    with open(DATA / "skills.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            data = json.loads(FILE.read_text())
            if not isinstance(data.get("skills"), list):
                raise ValueError
        except (OSError, ValueError, AttributeError):
            data = {"version": 1, "skills": []}
        yield data
        FILE.write_text(json.dumps(data, indent=1))


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]{3,}", (text or "").lower())) - {"the", "and", "for", "with", "that", "this", "into"}


def _clean(skill: dict, private: list[str] | None = None) -> dict:
    """Clip and sanitize a skill's text so it fits the store and carries no personal details."""
    s = lambda t, n=STEP_CHARS: memory.sanitize(t, private)[:n]
    return {"name": s(skill.get("name") or "Untitled Skill", 60).strip() or "Untitled Skill",
            "where": s(skill.get("where") or "general", 60).lower().strip() or "general",
            "when": s(skill.get("when") or ""),
            "steps": [s(x) for x in (skill.get("steps") or []) if str(x).strip()][:MAX_STEPS],
            "notes": [s(x) for x in (skill.get("notes") or []) if str(x).strip()][:8]}


def _value(sk: dict) -> float:
    """How much a skill is worth keeping; the user's own are worth the most."""
    age_days = (time.time() - sk.get("updated", 0)) / 86400
    base = 100.0 if sk.get("source") == "user" else 0.0
    return base + 2 * sk.get("wins", 0) - 3 * sk.get("fails", 0) + 0.5 * sk.get("uses", 0) - age_days / 30


def _similar(a: dict, b: dict) -> bool:
    if a["where"] != b["where"]:
        return False
    wa, wb = _words(a["name"] + " " + a["when"]), _words(b["name"] + " " + b["when"])
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= 0.5


def save(skill: dict, source: str = "user", private: list[str] | None = None) -> dict:
    """Add a skill, or update the similar one already kept for the same place. Returns the stored skill."""
    new = _clean(skill, private)
    if not new["steps"]:
        raise ValueError("a skill needs at least one step")
    now = time.time()
    with _locked() as data:
        for old in data["skills"]:
            if _similar(old, new):
                if source == "user" or old.get("source") != "user":  # never overwrite the user's own with a learned one
                    old.update(steps=new["steps"], when=new["when"] or old.get("when", ""))
                old["notes"] = list(dict.fromkeys([*old.get("notes", []), *new["notes"]]))[:8]
                old["updated"] = now
                if source == "user":
                    old["source"] = "user"
                return dict(old)
        stored = {"id": uuid.uuid4().hex[:12], **new, "source": source, "uses": 0, "wins": 0, "fails": 0,
                  "created": now, "updated": now}
        data["skills"].append(stored)
        _trim(data)
        return dict(stored)


def _trim(data: dict):
    """Drop learned skills that keep failing, then the least valuable, to stay under MAX_SKILLS."""
    data["skills"] = [s for s in data["skills"]
                      if s.get("source") == "user" or s.get("fails", 0) < 3 or s.get("wins", 0) > s.get("fails", 0)]
    if len(data["skills"]) > MAX_SKILLS:
        data["skills"].sort(key=_value, reverse=True)
        del data["skills"][MAX_SKILLS:]


def find(task: str, where: list[str] | None = None, limit: int = 2) -> list[dict]:
    """Skills that fit this task, best first (by place and by words shared with the name and 'when')."""
    words = _words(memory.QUOTED.sub("", task or ""))
    places = {w.lower() for w in (where or [])}
    scored = []
    for s in all_skills():
        overlap = len(words & _words(s["name"] + " " + s.get("when", "")))
        if overlap == 0 or (places and s["where"] not in places and s["where"] != "general"):
            continue
        scored.append((overlap + (1 if s["where"] in places else 0) + 0.1 * _value(s) / 100, s))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored[:limit]]


def as_text(skill: dict) -> str:
    steps = "\n".join(f"  {i}. {st}" for i, st in enumerate(skill["steps"], 1))
    notes = "".join(f"\n  Note: {n}" for n in skill.get("notes", []))
    return f"Skill \"{skill['name']}\" ({skill['where']}):\n{steps}{notes}"


def result(skill_id: str, ok: bool):
    """Count a use of a skill and whether the task worked; learned skills that keep failing are dropped."""
    with _locked() as data:
        for s in data["skills"]:
            if s["id"] == skill_id:
                s["uses"] = s.get("uses", 0) + 1
                s["wins" if ok else "fails"] = s.get("wins" if ok else "fails", 0) + 1
                s["updated"] = time.time()
        _trim(data)


def all_skills() -> list[dict]:
    with _locked() as data:
        return [dict(s) for s in data["skills"]]


def delete(skill_id: str):
    with _locked() as data:
        data["skills"] = [s for s in data["skills"] if s["id"] != skill_id]


# --- learning: from doing, from a finished task on request, from a video --------------------

_tally: dict[str, int] = {}  # successes per kind of task in this process (bounded below)


def _kind(task: str) -> str:
    """A rough key for 'the same kind of task': the place plus the leading verbs and nouns."""
    words = [w for w in re.findall(r"[a-z]{3,}", memory.QUOTED.sub("", (task or "").lower()))][:8]
    return " ".join(sorted(set(words)))


def distill(task: str, log: list[str], ask_json: Callable[[str, str], dict | None]) -> dict | None:
    """Ask a vision model to turn a run that worked into a skill (None if there's no model to ask)."""
    actions = [ln.strip() for ln in log if "→" in ln or ln.strip().startswith(("•", "ok:"))][-40:]
    try:
        out = ask_json(DISTILL_SYSTEM, json.dumps({"task": task, "actions_that_ran": actions}))
    except Exception:
        return None
    return out if isinstance(out, dict) and out.get("steps") else None


def learn(task: str, log: list[str], ok: bool, retried: bool, ask_json: Callable[[str, str], dict | None],
          where: list[str] | None = None) -> dict | None:
    """After a task: maybe make a skill from it. Only when it's worth keeping: the task worked, and
    either it took a recovery to get right (hard-won), or the same kind of task has now worked
    AUTO_MIN_WINS times. The model must also judge it reusable. Nothing is learned from failures."""
    if not ok:
        return None
    key = _kind(task)
    _tally[key] = _tally.get(key, 0) + 1
    if len(_tally) > 200:  # bounded: forget the oldest kinds
        for k in list(_tally)[:100]:
            del _tally[k]
    if not (retried or _tally[key] >= AUTO_MIN_WINS):
        return None
    skill = distill(task, log, ask_json)
    if not skill or not skill.get("worth_keeping", True):
        return None
    if where and not skill.get("where"):
        skill["where"] = where[0]
    return save(skill, "learned", memory.private_bits(task))


def from_run(task: str, log: list[str], ask_json: Callable[[str, str], dict | None], name: str = "") -> dict:
    """The user asked to keep how a task was just done. Uses a vision model to generalise when there
    is one; otherwise keeps the actions that ran, cleaned up."""
    skill = distill(task, log, ask_json) or {
        "name": name or " ".join(memory.QUOTED.sub("", task).split()[:5]).title(),
        "where": (memory.places(task) or ["general"])[0], "when": f"Requests like: {memory.QUOTED.sub('{text}', task)}",
        "steps": [re.sub(r"^\s*(\w+ → |• )", "", ln).strip() for ln in log if "→" in ln or ln.strip().startswith("•")]}
    if name:
        skill["name"] = name
    return save(skill, "user", memory.private_bits(task))


def contact_sheet(video_path: str, frames: int = 12, width: int = 1600) -> bytes:
    """Evenly spaced frames of a video, tiled into one PNG (so any vision model can read them)."""
    from AVFoundation import AVAssetImageGenerator, AVURLAsset
    from CoreMedia import CMTimeGetSeconds, CMTimeMakeWithSeconds
    from Foundation import NSURL
    from PIL import Image
    from AppKit import NSBitmapImageFileTypePNG, NSBitmapImageRep

    asset = AVURLAsset.URLAssetWithURL_options_(NSURL.fileURLWithPath_(str(video_path)), None)
    seconds = CMTimeGetSeconds(asset.duration())
    if not seconds or seconds != seconds:
        raise ValueError("couldn't read that video")
    gen = AVAssetImageGenerator.assetImageGeneratorWithAsset_(asset)
    gen.setAppliesPreferredTrackTransform_(True)
    shots = []
    for i in range(frames):
        t = CMTimeMakeWithSeconds(seconds * (i + 0.5) / frames, 600)
        got = gen.copyCGImageAtTime_actualTime_error_(t, None, None)
        cg = got[0] if isinstance(got, tuple) else got
        if cg is None:
            continue
        rep = NSBitmapImageRep.alloc().initWithCGImage_(cg)
        png = bytes(rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, None))
        shots.append(Image.open(io.BytesIO(png)).convert("RGB"))
        del cg
    if not shots:
        raise ValueError("couldn't read frames from that video")
    cols = 3 if len(shots) > 4 else 2
    rows = -(-len(shots) // cols)
    cell_w = width // cols
    cell_h = int(cell_w * shots[0].height / shots[0].width)
    sheet = Image.new("RGB", (cell_w * cols, cell_h * rows), "white")
    for i, im in enumerate(shots):
        sheet.paste(im.resize((cell_w - 4, cell_h - 4)), ((i % cols) * cell_w + 2, (i // cols) * cell_h + 2))
    buf = io.BytesIO()
    sheet.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def from_video(video_path: str, ask_image_json: Callable[[str, str, bytes], dict | None], name: str = "") -> dict:
    """Watch a screen recording and keep what it shows as a skill (needs a vision model)."""
    skill = ask_image_json(VIDEO_SYSTEM, json.dumps({"hint": name}) if name else "{}", contact_sheet(video_path))
    if not isinstance(skill, dict) or not skill.get("steps"):
        raise ValueError("the vision model couldn't tell what the video shows")
    if name:
        skill["name"] = name
    return save(skill, "video")


# --- sharing ---------------------------------------------------------------------------------

def export(path, ids: list[str] | None = None) -> int:
    """Write skills (all, or the chosen ones) to a pack another person can import; returns how many."""
    chosen = [s for s in all_skills() if not ids or s["id"] in ids]
    pack = [{k: s[k] for k in ("name", "where", "when", "steps", "notes")} for s in chosen]
    pack = [_clean(s) for s in pack]  # sanitized again on the way out
    with open(path, "w") as f:
        json.dump({"format": PACK_FORMAT, "version": 1, "skills": pack}, f, indent=1)
    return len(pack)


def import_(path) -> int:
    with open(path) as f:
        data = json.load(f)
    if data.get("format") != PACK_FORMAT or not isinstance(data.get("skills"), list):
        raise ValueError("not a Computer Use skill pack")
    n = 0
    for s in data["skills"][:MAX_SKILLS]:
        if isinstance(s, dict) and s.get("steps"):
            save(s, "imported")
            n += 1
    return n


def categories() -> dict[str, list[dict]]:
    """Skills grouped by app or site, for a list in the UI."""
    out: dict[str, list[dict]] = {}
    for s in sorted(all_skills(), key=lambda s: (s["where"], s["name"])):
        out.setdefault(s["where"], []).append(s)
    return out
