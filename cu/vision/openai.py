"""OpenAI as the vision model, through the computer tool of the Responses API.

Each computer_call carries an ordered list of actions (click, type, keypress, scroll…) in
screenshot pixels; we run them, then answer with a screenshot. The server keeps the conversation
(previous_response_id), so only the latest screenshot is held here."""

import json
import os
from typing import Callable

import openai

from .. import actions, narrate as narration
from .base import (CU_SYSTEM, OPEN_APP_DESC, OPEN_APP_SCHEMA, YIELD_DESC, YIELD_SCHEMA, Declined, Guard, Shot,
                   VisionModel, data_url, holding, open_app, sleep)

TOOLS = [
    {"type": "computer"},
    {"type": "function", "name": "open_app", "description": OPEN_APP_DESC, "parameters": OPEN_APP_SCHEMA},
    {"type": "function", "name": "yield_to_user", "description": YIELD_DESC, "parameters": YIELD_SCHEMA},
]
BUTTONS = {"left": "left", "right": "right", "wheel": "middle"}


def keys() -> list[str]:
    return [k for k in (os.environ.get("OPENAI_API_KEY"),) if k]


def _dict(obj) -> dict:
    return obj.model_dump(exclude_none=True) if hasattr(obj, "model_dump") else dict(obj or {})


def _usage(resp) -> tuple[int, int]:
    u = getattr(resp, "usage", None)
    return (getattr(u, "input_tokens", 0) or 0, getattr(u, "output_tokens", 0) or 0) if u else (0, 0)


class OpenAI(VisionModel):
    provider, label = "openai", "OpenAI"

    def __init__(self, models: list[str], auto_switch: bool = True, log: Callable[[str], None] = print,
                 api_key: str | None = None, client=None):
        key = api_key or next(iter(keys()), None)
        if client is None and not key:
            raise RuntimeError("Set OPENAI_API_KEY to use OpenAI")
        super().__init__(models, auto_switch, log)
        self.client = client or openai.OpenAI(api_key=key)

    def _request(self, model: str, **kw):
        resp = self.client.responses.create(model=model, **kw)
        return (resp, *_usage(resp))

    def _json(self, system: str, text: str, png: bytes) -> str:
        resp = self._create(instructions=system + "\nReply with the JSON object only.", input=[{
            "role": "user",
            "content": [{"type": "input_text", "text": text}, {"type": "input_image", "image_url": data_url(png)}],
        }])
        return resp.output_text or "{}"

    def _act(self, instruction: str, max_turns: int = 15, confirm: Callable[[str], bool] = lambda m: False,
             log: Callable[[str], None] = print, stop=None, narrate: Callable[[str], None] = lambda t: None,
             allow_risky: bool = False, confirm_all: bool = False) -> tuple[bool, str]:
        guard = Guard(confirm, allow_risky, confirm_all, task=instruction)
        shot = Shot()
        resp = self._create(instructions=CU_SYSTEM, tools=TOOLS, input=[{
            "role": "user",
            "content": [{"type": "input_text", "text": instruction},
                        {"type": "input_image", "image_url": data_url(shot.png), "detail": "original"}],
        }])
        for _ in range(max_turns):
            if stop is not None and stop.is_set():
                return False, "stopped by user"
            calls = [o for o in resp.output if o.type in ("computer_call", "function_call")]
            if not calls:
                return True, resp.output_text or "done"
            replies = []
            for call in calls:
                if call.type == "function_call":
                    args = json.loads(call.arguments or "{}")
                    log(f"  {self.label.lower()} → {call.name} {args}")
                    if call.name == "yield_to_user":
                        return False, f"needs user: {args.get('reason')}"
                    narrate(narration.vision_action(call.name, args))
                    try:
                        open_app(args["app_name"])
                        out = {"status": "ok"}
                    except Exception as e:
                        out = {"status": "error", "error": str(e)}
                    replies.append({"type": "function_call_output", "call_id": call.call_id, "output": json.dumps(out)})
                    continue
                acknowledged = []
                for check in call.pending_safety_checks or []:
                    if not confirm(f"The vision model wants to continue: {check.message or check.code}. Allow?"):
                        raise Declined(check.message or check.code or "a safety check")
                    acknowledged.append({"id": check.id, "code": check.code, "message": check.message})
                batch = list(call.actions or ([] if call.action is None else [call.action]))
                for act in batch:
                    a = _dict(act)
                    log(f"  {self.label.lower()} → {a}")
                    narrate(narration.vision_action(a.get("type", ""), a))
                    try:
                        self.run(a, shot, guard, stop)
                    except (actions.pyautogui.FailSafeException, Declined):
                        raise
                    except Exception as e:  # the rest of the batch assumed this worked; the screenshot shows what happened
                        log(f"    failed: {e}")
                        break
                shot = Shot()
                reply = {"type": "computer_call_output", "call_id": call.call_id,
                         "output": {"type": "computer_screenshot", "image_url": data_url(shot.png), "detail": "original"}}
                if acknowledged:
                    reply["acknowledged_safety_checks"] = acknowledged
                replies.append(reply)
            resp = self._create(mid_conversation=True, previous_response_id=resp.id, instructions=CU_SYSTEM,
                                tools=TOOLS, input=replies)
        return False, "ran out of turns"

    @staticmethod
    def run(a: dict, shot: Shot, guard: Guard, stop=None):
        """Execute one computer action. Coordinates are screenshot pixels."""
        kind = a.get("type")
        if kind in ("click", "double_click"):
            x, y = shot.point(a["x"], a["y"])
            guard.click(x, y, kind.replace("_", " "))
            with holding(a.get("keys")):
                actions.click(x, y, button=BUTTONS.get(a.get("button", "left"), "left"),
                              clicks=2 if kind == "double_click" else 1)
        elif kind == "drag":
            path = [shot.point(p["x"], p["y"]) for p in a.get("path") or []]
            if len(path) < 2:
                raise ValueError("drag needs at least two points")
            guard.other(f"drag from {path[0]} to {path[-1]}")
            with holding(a.get("keys")):
                actions.drag(*path[0], *path[-1])
        elif kind == "move":
            actions.move_to(*shot.point(a["x"], a["y"]))
        elif kind == "scroll":
            x, y = shot.point(a["x"], a["y"])
            sx, sy = int(a.get("scroll_x") or 0), int(a.get("scroll_y") or 0)
            with holding(a.get("keys")):
                if sy:
                    actions.scroll(x, y, "down" if sy > 0 else "up", abs(sy))
                if sx:
                    actions.scroll(x, y, "right" if sx > 0 else "left", abs(sx))
        elif kind == "keypress":
            combo = list(a.get("keys") or [])
            guard.keys(combo)
            actions.hotkey(combo)
        elif kind == "type":
            guard.other(f'type "{a.get("text", "")}"')
            actions.type_text(a.get("text", ""))
        elif kind == "wait":
            sleep(1, stop)
        elif kind == "screenshot":
            pass
        else:
            raise ValueError(f"unsupported action {kind}")
