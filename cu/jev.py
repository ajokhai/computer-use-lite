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
    "COPY": "Copy the current selection or link to clipboard (press Command-C).",
    "SELECT_ALL": "Select all text or content in the current field/window (press Command-A).",
    "CLOSE_WINDOW": "Close the frontmost window or tab when finished with it (press Command-W).",
    "QUIT_APP": "Quit the current application when its task is complete (press Command-Q).",
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
        self.http = httpx.Client(
            timeout=30,
            headers={
                "Authorization": f"Bearer {key}",
                "TypeSafe-Danger-Zone-Images": "true",
            },
        )
        self.input_tokens = 0

    def ask(self, state, questions: dict, assets: dict | None = None, model: str | None = None) -> dict:
        s = usage.status("jev")
        if not s["available"]:
            raise usage.QuotaExhausted("jev", s["blocked_until"])
        chosen_model = model or ("jev-preview" if assets or any(q.get("type") == "box" for q in questions.values()) else MODEL)
        body = {"model": chosen_model, "state": state, "questions": questions}
        if assets:
            body["assets"] = assets
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

    def locate(self, target_description: str, png_bytes: bytes | None = None) -> tuple[tuple[int, int, int, int] | None, float]:
        """Locate the bounding box of a target visually in a screenshot using Jev Box primitive.
        Returns ((center_x, center_y, width, height), confidence)."""
        import base64
        import io
        from PIL import Image
        from .screen import screenshot_png, screen_size

        if png_bytes is None:
            png_bytes = screenshot_png(max_width=1440)

        im = Image.open(io.BytesIO(png_bytes))
        img_w, img_h = im.size
        sw, sh = screen_size()
        scale_x = sw / img_w
        scale_y = sh / img_h

        b64 = base64.b64encode(png_bytes).decode("utf-8")
        state = {"screen": "asset-screen"}
        assets = {"asset-screen": {"type": "image", "format": "png", "data_base64": b64}}
        questions = {
            "target": {
                "type": "box",
                "instructions": f"the {target_description} in `screen`",
            }
        }
        try:
            answers = self.ask(state, questions, assets=assets, model="jev-preview")
            res = answers.get("target", {})
            box = res.get("box")
            conf = float(res.get("confidence", 0.0))
            if box and conf >= 0.1:
                top = float(box["top"])
                bottom = float(box["bottom"])
                left = float(box["left"])
                right = float(box["right"])

                x1 = left * img_w * scale_x
                x2 = right * img_w * scale_x
                y1 = top * img_h * scale_y
                y2 = bottom * img_h * scale_y

                cx = int((x1 + x2) / 2)
                cy = int((y1 + y2) / 2)
                w = int(x2 - x1)
                h = int(y2 - y1)
                return (cx, cy, w, h), conf
        except Exception:
            pass
        return None, 0.0

    def visual_check(self, claim: str, png_bytes: bytes | None = None) -> float:
        """Check whether a claim is visually true on screen using Jev Noul primitive on a screenshot."""
        import base64
        from .screen import screenshot_png
        if png_bytes is None:
            png_bytes = screenshot_png(max_width=1000)
        b64 = base64.b64encode(png_bytes).decode("utf-8")
        state = {"screen": "asset-screen"}
        assets = {"asset-screen": {"type": "image", "format": "png", "data_base64": b64}}
        questions = {
            "check": {
                "type": "noul",
                "instructions": f"Is this claim visually true of `screen`? {claim}",
            }
        }
        try:
            answers = self.ask(state, questions, assets=assets, model="jev-preview")
            return float(answers.get("check", {}).get("noul", 0.0))
        except Exception:
            return 0.5

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

    def check(self, task: str, app: str, elements: list[Element], history: list[str] | None = None) -> float:
        """Probability that the task has been achieved given the execution history and current screen state."""
        state = {
            "task": task,
            "frontmost_app": app,
            "actions_completed": history[-10:] if history else [],
            "elements": [e.describe() for e in elements[:160]],
            "clipboard_has_content": _clipboard_has_content(),
        }
        answers = self.ask(state, {
            "ok": {
                "type": "noul",
                "instructions": (
                    "Given the actions already executed and the current application state, "
                    f"has this task been successfully accomplished? Task: '{task}'"
                ),
            }
        })
        return float(answers["ok"]["noul"])

    def decide(self, task: str, app: str, elements: list[Element], history: list[str],
               texts: list[str], apps: list[str], tried: list[str] | None = None) -> dict:
        """One request: the most probable next operation, its target, what to type, which app."""
        elements = elements[: MAX_OPTIONS - 2]
        focused = next((e.describe() for e in elements if e.focused), "nothing")
        has_clip = _clipboard_has_content()

        # Filter out actions that cannot be executed in this state (anti-stall pattern)
        active_ops = dict(OPS)
        if not has_clip:
            active_ops.pop("PASTE", None)
        if not texts:
            active_ops.pop("TYPE", None)

        state = {
            "task": task,
            "frontmost_app": app,
            "focused_element": focused,
            "clipboard_has_content": has_clip,
            "actions_so_far": history[-10:] or ["none yet"],
            "already_tried_on_this_screen": list(tried or []),
        }
        targets = {e.id: e.describe() for e in elements}
        targets[NOT_FOUND] = "None of these; no element is needed or the right one is not listed."
        questions = {
            "op": {
                "type": "choice",
                "instructions": (
                    "What is the single most sensible next action to make progress on the task, "
                    "given the actions already taken? Do not repeat an action that was just taken, "
                    "and NEVER pick an action listed in 'already_tried_on_this_screen': each of those led straight back here."
                ),
                "criteria": active_ops,
            },
            "target": {
                "type": "choice",
                "instructions": (
                    "Which on-screen element should the next action be performed on? "
                    "Do not pick an element if clicking it was already tried on this screen without changing state."
                ),
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
            "text": texts[int(a["text"]["choice"][1:])] if texts and "text" in a and a["text"]["choice"].startswith("t") else (texts[0] if texts else ""),
            "app": apps[int(a["app"]["choice"][1:])] if apps and "app" in a and a["app"]["choice"].startswith("a") else "",
        }

    @property
    def cost_usd(self) -> float:
        return self.input_tokens * 0.042 / 1e6


def _clipboard_has_content() -> bool:
    """Whether the clipboard holds anything. Its contents never leave the Mac:
    they may be a password or anything else the user copied."""
    try:
        from AppKit import NSPasteboard
        return bool(NSPasteboard.generalPasteboard().types())
    except Exception:
        return False


def text_candidates(task: str, limit: int = 60) -> list[str]:
    """Phrases from the task Jev may choose to type (native Rust backend with Python fallback)."""
    from .screen import native_json
    try:
        cands = native_json("cu_extract_text_candidates", task.encode("utf-8"), limit)
        if cands:
            return cands
    except Exception:
        pass

    # 0. URLs and domains (highest priority for browser/navigation tasks)
    urls = re.findall(r'https?://[^\s,;"\']+|www\.[^\s,;"\']+|\b[a-zA-Z0-9-]+\.(?:com|org|net|io|ai|dev|co|edu|gov|xyz)(?:/[^\s,;"\']*)?', task)
    out = list(urls)
    out += [m for pair in re.findall(r'"([^"]+)"|\u201c([^\u201d]+)\u201d|\'([^\']{2,})\'', task) for m in pair if m]
    for verb in ("type", "write", "enter", "search for", "search", "look up", "find", "named", "called",
                 "titled", "saying", "that says", "to say", "message", "subject", "note", "to", "for", "about",
                 "calculate", "compute", "solve", "math", "evaluate", "with", "recipient", "contact",
                 "chat with", "send to", "stating", "comment", "tweet", "open", "visit", "go to"):
        out += [m.strip(" .") for m in re.findall(rf"\b{verb}\b[:\s]+(.+?)(?=$|[.;]| and then | then )", task, re.I)]
    out += re.findall(r"[\d\.\,]+(?:\s*[\+\-\*\/\=\%]\s*[\d\.\,]+)+", task)
    out += re.findall(r"\b\d+\b", task)
    out += re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", task)

    # Protect '://' when splitting clauses
    task_safe = re.sub(r'https?://', '___PROTO___', task)
    for p in re.split(r"[,;:]| and | then ", task_safe):
        restored = p.replace('___PROTO___', 'https://').strip(" .")
        if restored:
            out.append(restored)
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
