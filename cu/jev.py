"""TypeSafe Jev client. Jev is text-only and answers typed questions, so it grounds a
planned step to one element of the accessibility tree instead of looking at pixels."""

import os
import re
import time
from functools import lru_cache
from pathlib import Path

import httpx

from . import usage
from .screen import Element

URL = os.environ.get("JEV_URL", "https://api.typesafe.ai/v1/systemone")
MODEL = os.environ.get("JEV_MODEL", "jev-latest")
MAX_OPTIONS = 255
NOT_FOUND = "NOT_FOUND"

OPS = {
    "CLICK": "Click the target element (button, link, menu item, tab, checkbox, list row).",
    "DOUBLE_CLICK": "Double-click the target element (open a file or item).",
    "TYPE": "Type text into the target text field, or into the focused field if no target.",
    "PRESS_ENTER": "Press Return to submit or confirm what was just typed.",
    "PRESS_ESCAPE": "Press Escape to dismiss a dialog, popup or menu.",
    "PRESS_TAB": "Press Tab to move to the next field.",
    "NEW_ITEM": "Press Command-N to create a new document, note, message or window.",
    "SCROLL_DOWN": "Scroll down to reveal more content.",
    "SCROLL_UP": "Scroll up.",
    "OPEN_APP": "Launch or switch to an application; the needed app is not the frontmost app.",
    "PASTE": "Paste the clipboard content (press Command-V) into the active note, document, or field.",
    "SCREENSHOT": "Take a screenshot of the current screen/window and copy it to the clipboard.",
    "WAIT": "Wait for something to finish loading.",
    "DONE": "The task is fully complete; nothing left to do.",
    "BLOCKED": "Cannot proceed: needs a password, a decision, or something not on screen.",
}


class Jev:
    def __init__(self, api_key: str | None = None):
        key = api_key or os.environ.get("TYPESAFE_API_KEY") or os.environ.get("JEV_API_KEY")
        if not key:
            raise RuntimeError("Set TYPESAFE_API_KEY to use Jev")
        self.http = httpx.Client(timeout=30, headers={"Authorization": f"Bearer {key}"})
        self.input_tokens = 0

    def ask(self, state, questions: dict) -> dict:
        s = usage.status("jev")
        if not s["available"]:
            raise usage.QuotaExhausted("jev", s["blocked_until"])
        body = {"model": MODEL, "state": state, "questions": questions}
        for attempt in range(4):
            r = self.http.post(URL, json=body)
            if r.status_code in (429, 529):
                time.sleep(0.5 * 2**attempt)
                continue
            if r.status_code in (402, 403):
                usage.mark_exhausted("jev", None)
                raise usage.QuotaExhausted("jev", None, r.text[:120])
            r.raise_for_status()
            data = r.json()
            tokens = data.get("usage", {}).get("input_tokens", 0)
            self.input_tokens += tokens
            usage.record("jev", tokens, data.get("usage", {}).get("output_tokens", 0))
            return data["answers"]
        usage.mark_exhausted("jev", time.time() + 60)
        raise usage.QuotaExhausted("jev", time.time() + 60, "rate limited")

    def ground(self, step: dict, app: str, elements: list[Element]) -> tuple[Element | None, float]:
        """Pick the element a step refers to. Returns (element or None, probability)."""
        elements = elements[: MAX_OPTIONS - 1]
        if not elements:
            return None, 0.0
        criteria = {e.id: e.describe() for e in elements}
        criteria[NOT_FOUND] = "None of the listed elements matches the target."
        state = {
            "frontmost_app": app,
            "step": step.get("do", ""),
            "action": step["kind"],
            "target": step.get("target", ""),
        }
        answers = self.ask(state, {
            "target": {
                "type": "choice",
                "instructions": (
                    "Which on-screen element should the action be performed on to carry out "
                    "this step? For typing, choose the text field to type into. "
                    "Prefer exact label matches."
                ),
                "criteria": criteria,
            }
        })
        a = answers["target"]
        pick = a["choice"]
        prob = float(a.get("probabilities", {}).get(pick, a.get("confidence", 0)))
        if pick == NOT_FOUND:
            return None, prob
        return next((e for e in elements if e.id == pick), None), prob

    def check(self, expect: str, app: str, elements: list[Element]) -> float:
        """Probability that the expected outcome is visible in the AX tree."""
        state = {
            "frontmost_app": app,
            "elements": [e.describe() for e in elements[:200]],
        }
        answers = self.ask(state, {
            "ok": {"type": "noul", "instructions": f"Is this true of the current screen? {expect}"}
        })
        return float(answers["ok"]["noul"])

    def decide(self, task: str, app: str, elements: list[Element], history: list[str],
               texts: list[str], apps: list[str]) -> dict:
        """One request: the most probable next operation, its target, what to type, which app."""
        elements = elements[: MAX_OPTIONS - 2]
        focused = next((e.describe() for e in elements if e.focused), "nothing")
        state = {
            "task": task,
            "frontmost_app": app,
            "focused_element": focused,
            "actions_so_far": history[-10:] or ["none yet"],
        }
        targets = {e.id: e.describe() for e in elements}
        targets[NOT_FOUND] = "None of these; no element is needed or the right one is not listed."
        questions = {
            "op": {
                "type": "choice",
                "instructions": ("What is the single most sensible next action to make progress on the task, "
                                 "given the actions already taken? Don't repeat an action that already succeeded."),
                "criteria": OPS,
            },
            "target": {
                "type": "choice",
                "instructions": "Which on-screen element should the next action be performed on?",
                "criteria": targets,
            },
            "text": {
                "type": "choice",
                "instructions": "If the next action is typing, which exact text from the task should be typed now?",
                "criteria": {f"t{i}": t for i, t in enumerate(texts)},
            },
        }
        if apps:
            questions["app"] = {
                "type": "choice",
                "instructions": "Which application is needed for the task right now?",
                "criteria": {f"a{i}": a for i, a in enumerate(apps)},
            }
        a = self.ask(state, questions)
        prob = lambda q: float(a[q].get("probabilities", {}).get(a[q]["choice"], a[q].get("confidence", 0)))
        pick = a["target"]["choice"]
        return {
            "op": a["op"]["choice"],
            "op_p": prob("op"),
            "element": next((e for e in elements if e.id == pick), None),
            "target_p": prob("target"),
            "text": texts[int(a["text"]["choice"][1:])],
            "app": apps[int(a["app"]["choice"][1:])] if apps else "",
        }

    @property
    def cost_usd(self) -> float:
        return self.input_tokens * 0.042 / 1e6


def text_candidates(task: str, limit: int = 60) -> list[str]:
    """Phrases from the task Jev may choose to type (native Rust backend with Python fallback)."""
    from .screen import _load_native_lib
    lib = _load_native_lib()
    if lib and hasattr(lib, "cu_extract_text_candidates"):
        try:
            import json
            raw = lib.cu_extract_text_candidates(task.encode("utf-8"), limit)
            if raw:
                cands = json.loads(raw.decode("utf-8"))
                if cands:
                    return cands
        except Exception:
            pass

    out = [m for pair in re.findall(r'"([^"]+)"|\u201c([^\u201d]+)\u201d|\'([^\']{2,})\'', task) for m in pair if m]
    for verb in ("type", "write", "enter", "search for", "search", "look up", "find", "named", "called",
                 "titled", "saying", "that says", "to say", "message", "subject", "note", "to", "for", "about",
                 "calculate", "compute", "solve", "math", "evaluate"):
        out += [m.strip(" .") for m in re.findall(rf"\b{verb}\b[:\s]+(.+?)(?=$|[.;]| and then | then )", task, re.I)]
    out += re.findall(r"[\d\.\,]+(?:\s*[\+\-\*\/\=\%]\s*[\d\.\,]+)+", task)
    out += re.findall(r"\b\d+\b", task)
    out += re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+|https?://\S+|\b[\w-]+\.(?:com|org|net|io|ai|dev|co)\S*", task)
    if ":" in task:
        out.append(task.split(":", 1)[1].strip(" ."))
    out += [p.strip(" .") for p in re.split(r"[,;:]| and | then ", task)]
    out.append(task.strip())
    seen, uniq = set(), []
    for t in out:
        if t and t.lower() not in seen and len(t) <= 500:
            seen.add(t.lower())
            uniq.append(t)
    return uniq[:limit]


@lru_cache(maxsize=1)
def _all_apps() -> tuple[str, ...]:
    roots = ["/Applications", "/System/Applications", "/System/Applications/Utilities", str(Path.home() / "Applications")]
    names = {p.stem for r in roots if Path(r).is_dir() for p in Path(r).glob("*.app")}
    return tuple(sorted(names))


def installed_apps(limit: int = MAX_OPTIONS) -> list[str]:
    return list(_all_apps()[:limit])
