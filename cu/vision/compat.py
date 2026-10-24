"""Any model behind an OpenAI-compatible Chat Completions endpoint: OpenRouter (every model it
offers) or a local server such as Ollama or LM Studio.

These have no built-in computer tool, so each turn sends one screenshot plus the frontmost app's
accessibility elements, and the model replies with JSON actions. It can click an element by id,
which works even for small or text-only models, or a point in screenshot pixels. Only the latest
screenshot and a short text history are kept."""

import json
import os
import re
from typing import Callable

import openai

from .. import actions, narrate as narration
from ..screen import ax_elements
from .base import Declined, Guard, Shot, VisionModel, data_url, open_app, parse_json, sleep

OPENROUTER_URL = "https://openrouter.ai/api/v1"

ACT_SYSTEM = """You operate a macOS desktop for the user. Complete the task with the fewest actions.
Keyboard shortcuts use cmd (not ctrl). Each turn you get the task, what you did so far,
a screenshot (its size in pixels is given) and the frontmost app's accessibility elements, each with an id.

Reply with JSON only: {"status": "continue"|"done"|"blocked", "message": "<one sentence>", "actions": [action, ...]}
Actions, run in order (give 1 to 4, then look again):
  {"type": "click", "element": "<id>"}  or  {"type": "click", "x": <px>, "y": <px>}
  {"type": "double_click" | "right_click", same fields as click}
  {"type": "type", "text": "<literal text>", "element": "<id, optional>", "enter": false}
  {"type": "key", "keys": ["cmd", "t"]}
  {"type": "scroll", "direction": "up"|"down", "element": "<id, optional>"}
  {"type": "drag", "x1": px, "y1": px, "x2": px, "y2": px}
  {"type": "open_app", "app": "Safari"}
  {"type": "open_url", "url": "https://..."}
  {"type": "wait", "seconds": 1}
Prefer element ids over pixel coordinates. status "done" when the task is complete (no actions);
"blocked" when you need the user (credentials, 2FA, a decision), with the reason in message."""


def _usage(resp) -> tuple[int, int]:
    u = getattr(resp, "usage", None)
    return (getattr(u, "prompt_tokens", 0) or 0, getattr(u, "completion_tokens", 0) or 0) if u else (0, 0)


class Compatible(VisionModel):
    """Model ids are "<provider>:<model>", so usage is counted per provider and model."""

    url = ""
    key_names: tuple[str, ...] = ()

    def __init__(self, models: list[str], auto_switch: bool = True, log: Callable[[str], None] = print,
                 url: str | None = None, api_key: str | None = None, client=None):
        super().__init__([m if m.startswith(f"{self.provider}:") else f"{self.provider}:{m}" for m in models if m],
                         auto_switch, log)
        key = api_key or next((os.environ[n] for n in self.key_names if os.environ.get(n)), None)
        if client is None and self.key_names and not key:
            raise RuntimeError(f"Set {self.key_names[0]} to use {self.label}")
        self.client = client or openai.OpenAI(base_url=url or self.url, api_key=key or "not-needed", max_retries=1)

    def unreachable(self, err: Exception) -> bool:
        return isinstance(err, openai.APIConnectionError)

    def _request(self, model: str, **kw):
        resp = self.client.chat.completions.create(model=model.split(":", 1)[1], **kw)
        return (resp, *_usage(resp))

    def _chat(self, system: str, text: str, png: bytes) -> str:
        resp = self._create(messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": [{"type": "text", "text": text},
                                         {"type": "image_url", "image_url": {"url": data_url(png)}}]},
        ])
        return (resp.choices[0].message.content or "") if resp.choices else ""

    def _json(self, system: str, text: str, png: bytes) -> str:
        return self._chat(system + "\nReply with the JSON object only.", text, png)

    def _act(self, instruction: str, max_turns: int = 15, confirm: Callable[[str], bool] = lambda m: False,
             log: Callable[[str], None] = print, stop=None, narrate: Callable[[str], None] = lambda t: None,
             allow_risky: bool = False, confirm_all: bool = False) -> tuple[bool, str]:
        guard = Guard(confirm, allow_risky, confirm_all, task=instruction)
        history: list[str] = []
        for _ in range(max_turns):
            if stop is not None and stop.is_set():
                return False, "stopped by user"
            shot = Shot()
            app, els = ax_elements(limit=120)
            context = {
                "task": instruction,
                "screenshot_size": [shot.width, shot.height],
                "frontmost_app": app,
                "elements": {e.id: e.describe() for e in els},
                "done_so_far": history[-15:],
            }
            reply = parse_json(self._chat(ACT_SYSTEM, json.dumps(context), shot.png))  # stateless, so a model switch just carries on
            status, message = reply.get("status", "continue"), str(reply.get("message") or "")
            if status == "done":
                return True, message or "done"
            if status == "blocked":
                return False, f"needs user: {message}"
            steps = [s for s in reply.get("actions") or [] if isinstance(s, dict)]
            if not steps:
                history.append("FAILED: no actions given; give actions, or status done/blocked")
                continue
            if message:
                narrate(message)
            byid = {e.id: e for e in els}
            for a in steps[:4]:
                log(f"  {self.label.lower()} → {a}")
                narrate(narration.vision_action(a.get("type", ""), a))
                try:
                    self.run(a, shot, byid, guard, stop)
                    history.append(f"ok: {json.dumps(a)}")
                except (actions.pyautogui.FailSafeException, Declined):
                    raise
                except Exception as e:
                    failed = f"FAILED: {json.dumps(a)} ({e})"
                    if history and history[-1].startswith(failed[:60]):  # the same thing failed again
                        failed += " · this failed twice: do something different (another element, the keyboard, a URL)"
                    history.append(failed)
                    break
            sleep(0.3, stop)
        return False, "ran out of turns"

    @staticmethod
    def run(a: dict, shot: Shot, byid: dict, guard: Guard, stop=None):
        kind = a.get("type")

        def target():
            el = byid.get(str(a.get("element") or ""))
            if el is not None:
                return el.x, el.y
            if "x" in a and "y" in a:
                return shot.point(a["x"], a["y"])
            return None

        if kind in ("click", "double_click", "right_click"):
            pt = target()
            if pt is None:
                raise ValueError(f"no element {a.get('element')!r} and no x/y")
            guard.click(*pt, kind.replace("_", " "))
            actions.click(*pt, button="right" if kind == "right_click" else "left", clicks=2 if kind == "double_click" else 1)
        elif kind == "type":
            pt = target()
            if pt is not None:
                guard.click(*pt, "click")
                actions.click(*pt)
            guard.other(f'type "{a.get("text", "")}"')
            if a.get("enter"):
                guard.keys(["enter"], f'type "{a.get("text", "")}" and press Return')
            actions.type_text(str(a.get("text", "")), press_enter=bool(a.get("enter")))
        elif kind == "key":
            combo = a.get("keys") or re.split(r"[+ ]", str(a.get("key", "")))
            combo = [k for k in combo if k]
            guard.keys(combo)
            actions.hotkey(combo)
        elif kind == "scroll":
            pt = target() or shot.point(shot.width / 2, shot.height / 2)
            actions.scroll(*pt, a.get("direction", "down"), 400)
        elif kind == "drag":
            p1, p2 = shot.point(a["x1"], a["y1"]), shot.point(a["x2"], a["y2"])
            guard.other(f"drag from {p1} to {p2}")
            actions.drag(*p1, *p2)
        elif kind == "open_app":
            open_app(a.get("app") or a.get("app_name"))
        elif kind == "open_url":
            guard.other(f"open {a['url']}")
            actions.open_url(a["url"])
        elif kind == "wait":
            sleep(a.get("seconds", 1), stop)
        else:
            raise ValueError(f"unsupported action {kind}")


class OpenRouter(Compatible):
    provider, label, url = "openrouter", "OpenRouter", OPENROUTER_URL
    key_names = ("OPENROUTER_API_KEY",)


class Local(Compatible):
    """A server on this Mac; no key needed. Not running → treated as unavailable for a minute."""

    provider, label = "local", "Local model"


def list_models(url: str, api_key: str = "not-needed") -> list[str]:
    """Model names the server offers (GET /models), for the menu's hint. Empty if it isn't reachable."""
    try:
        client = openai.OpenAI(base_url=url, api_key=api_key, max_retries=0, timeout=3)
        return [m.id for m in client.models.list()]
    except Exception:
        return []
