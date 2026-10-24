"""Claude as the vision model, through Anthropic's computer use toolset (computer_toolset_20260801).

Claude's calls are tool_use blocks named after the action ("left_click", "type", "key"…) with
toolset_name "computer", often several per turn. Coordinates are pixels of the screenshots we send.
The conversation is append-only: earlier screenshots stay, because editing history would
invalidate Claude's thinking blocks, so the turn limit is what bounds memory."""

import os
from typing import Callable

import anthropic

from .. import actions, narrate as narration
from .base import (CU_SYSTEM, OPEN_APP_DESC, OPEN_APP_SCHEMA, YIELD_DESC, YIELD_SCHEMA, Declined, Guard, Shot,
                   VisionModel, b64, holding, open_app, sleep)

TOOLS = [
    {"type": "computer_toolset_20260801", "configs": {"zoom": {"enabled": False}}},
    {"name": "open_app", "description": OPEN_APP_DESC, "input_schema": OPEN_APP_SCHEMA},
    {"name": "yield_to_user", "description": YIELD_DESC, "input_schema": YIELD_SCHEMA},
]
FALLBACK_BETA = "server-side-fallback-2026-07-01"  # on a refusal, the API reruns the request on a suitable model
NOT_EXECUTED = "Not executed: an earlier computer action in this turn failed."
CLICKS = {"left_click": ("left", 1), "right_click": ("right", 1), "middle_click": ("middle", 1),
          "double_click": ("left", 2), "triple_click": ("left", 3)}


def keys() -> list[str]:
    return [k for k in (os.environ.get("ANTHROPIC_API_KEY"),) if k]


def _image(png: bytes) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64(png)}}


def _input(block) -> dict:
    inp = block.input
    return inp.model_dump(exclude_none=True) if hasattr(inp, "model_dump") else dict(inp or {})


class Claude(VisionModel):
    provider, label = "claude", "Claude"

    def __init__(self, models: list[str], auto_switch: bool = True, log: Callable[[str], None] = print,
                 api_key: str | None = None, client=None):
        key = api_key or next(iter(keys()), None)
        if client is None and not key:
            raise RuntimeError("Set ANTHROPIC_API_KEY to use Claude")
        super().__init__(models, auto_switch, log)
        self.client = client or anthropic.Anthropic(api_key=key)
        self.fallbacks = True

    def _request(self, model: str, **kw):
        kw.setdefault("max_tokens", 16000)
        kw.setdefault("output_config", {"effort": "medium"})
        if self.fallbacks:
            try:
                resp = self.client.beta.messages.create(model=model, betas=[FALLBACK_BETA], fallbacks="default", **kw)
            except anthropic.BadRequestError as e:
                if "fallback" not in str(e).lower():
                    raise
                self.fallbacks = False  # not offered for this account or model: carry on without it
                resp = self.client.messages.create(model=model, **kw)
        else:
            resp = self.client.messages.create(model=model, **kw)
        u = resp.usage
        return resp, (u.input_tokens or 0), (u.output_tokens or 0)

    def _json(self, system: str, text: str, png: bytes) -> str:
        resp = self._create(system=system + "\nReply with the JSON object only.", max_tokens=4000,
                            messages=[{"role": "user", "content": [_image(png), {"type": "text", "text": text}]}])
        return "".join(b.text for b in resp.content if b.type == "text") or "{}"

    def _act(self, instruction: str, max_turns: int = 15, confirm: Callable[[str], bool] = lambda m: False,
             log: Callable[[str], None] = print, stop=None, narrate: Callable[[str], None] = lambda t: None,
             allow_risky: bool = False, confirm_all: bool = False) -> tuple[bool, str]:
        guard = Guard(confirm, allow_risky, confirm_all)
        shot = Shot()
        messages = [{"role": "user", "content": [{"type": "text", "text": instruction}, _image(shot.png)]}]
        for turn in range(max_turns):
            if stop is not None and stop.is_set():
                return False, "stopped by user"
            resp = self._create(mid_conversation=turn > 0, system=CU_SYSTEM, tools=TOOLS, messages=messages)
            if resp.stop_reason == "refusal":
                return False, "the vision model declined this task"
            messages.append({"role": "assistant", "content": resp.content})  # unchanged, thinking blocks included
            uses = [b for b in resp.content if b.type == "tool_use"]
            said = " ".join(b.text for b in resp.content if b.type == "text").strip()
            if not uses:
                return True, said or "done"
            if said:
                narrate(said)
            results, failed, looked = [], False, False
            for b in uses:
                result = {"type": "tool_result", "tool_use_id": b.id}
                toolset = getattr(b, "toolset_name", None)
                if toolset:
                    result["toolset_name"] = toolset
                args = _input(b)
                if failed:
                    result.update(content=NOT_EXECUTED, is_error=True)
                    results.append(result)
                    continue
                log(f"  {self.label.lower()} → {b.name} {args}")
                if b.name == "yield_to_user" and not toolset:
                    return False, f"needs user: {args.get('reason')}"
                narrate(narration.vision_action(b.name, args))
                try:
                    out = self.run(b.name, args, shot, guard, stop)
                    if b.name == "screenshot":
                        shot = Shot()
                        out, looked = [_image(shot.png)], True
                    result["content"] = out
                except (actions.pyautogui.FailSafeException, Declined):
                    raise
                except Exception as e:  # report back so the model can recover
                    result.update(content=f"Error: {e}", is_error=True)
                    failed = True
                results.append(result)
            if not looked:  # let Claude see the effect of the batch without another round trip
                shot = Shot()
                last = results[-1]
                text = last.get("content") if isinstance(last.get("content"), str) else "OK"
                last["content"] = [{"type": "text", "text": text}, _image(shot.png)]
            messages.append({"role": "user", "content": results})
        return False, "ran out of turns"

    @staticmethod
    def run(name: str, a: dict, shot: Shot, guard: Guard, stop=None) -> str:
        """Execute one toolset member (or our open_app tool). Coordinates are screenshot pixels."""
        def at(key="coordinate"):
            if a.get(key):
                return shot.point(*a[key][:2])
            x, y = actions.pyautogui.position()
            return int(x), int(y)

        if name == "screenshot":
            return "OK"
        if name in CLICKS:
            button, clicks = CLICKS[name]
            x, y = at()
            guard.click(x, y, name.replace("_", " "))
            with holding(a.get("text")):
                actions.click(x, y, button=button, clicks=clicks)
        elif name == "left_click_drag":
            (x1, y1), (x2, y2) = at("start_coordinate"), at()
            guard.other(f"drag from {x1},{y1} to {x2},{y2}")
            with holding(a.get("text")):
                actions.drag(x1, y1, x2, y2)
        elif name == "mouse_move":
            actions.move_to(*at())
        elif name in ("left_mouse_down", "left_mouse_up"):
            actions._failsafe()
            guard.other(name.replace("_", " "))
            (actions.pyautogui.mouseDown if name == "left_mouse_down" else actions.pyautogui.mouseUp)()
        elif name == "cursor_position":
            x, y = actions.pyautogui.position()
            px, py = shot.pixel(x, y)
            return f"X={px},Y={py}"
        elif name == "scroll":
            x, y = at()
            with holding(a.get("text")):
                actions.scroll(x, y, a.get("scroll_direction", "down"), 50 * int(a.get("scroll_amount") or 3))
        elif name == "type":
            guard.other(f'type "{a.get("text", "")}"')
            actions.type_text(a.get("text", ""))
        elif name == "key":
            combo = [k for k in str(a.get("text", "")).split("+") if k]
            guard.keys(combo)
            for _ in range(max(1, min(int(a.get("repeat") or 1), 100))):
                actions.hotkey(combo)
        elif name == "hold_key":
            combo = [actions.key_name(k) for k in str(a.get("text", "")).split("+") if k]
            guard.keys(combo, "hold " + "+".join(combo))
            actions._failsafe()
            for k in combo:
                actions.pyautogui.keyDown(k)
            try:
                sleep(a.get("duration", 1), stop)
            finally:
                for k in reversed(combo):
                    actions.pyautogui.keyUp(k)
        elif name == "wait":
            sleep(a.get("duration", 1), stop)
        elif name == "open_app":
            open_app(a["app_name"])
        else:
            raise ValueError(f"unsupported action {name}")
        return "OK"
