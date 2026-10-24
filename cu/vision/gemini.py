"""Gemini as the vision model: plans tasks into atomic steps, and drives the desktop by vision
through its computer_use tool (coordinates on a 1000x1000 grid)."""

import json
import os
from typing import Callable

from google import genai

from .. import actions, narrate as narration
from ..screen import ax_elements, capture
from .base import CU_SYSTEM, OPEN_APP_DESC, OPEN_APP_SCHEMA, YIELD_DESC, YIELD_SCHEMA, Declined, VisionModel, b64

OPEN_APP_TOOL = {"type": "function", "name": "open_app", "description": OPEN_APP_DESC, "parameters": OPEN_APP_SCHEMA}
YIELD_TOOL = {"type": "function", "name": "yield_to_user", "description": YIELD_DESC, "parameters": YIELD_SCHEMA}


def _img(png: bytes) -> dict:
    return {"type": "image", "data": b64(png), "mime_type": "image/png"}


def _get(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def keys() -> list[str]:
    return [k for k in (os.environ.get("GEMINI_API_KEY"), os.environ.get("GOOGLE_API_KEY")) if k]


class Gemini(VisionModel):
    provider, label = "gemini", "Gemini"

    def __init__(self, models: list[str], auto_switch: bool = True, log: Callable[[str], None] = print,
                 api_key: str | None = None):
        key = api_key or next(iter(keys()), None)
        if not key:
            raise RuntimeError("Set GEMINI_API_KEY to use Gemini")
        super().__init__(list(filter(None, [os.environ.get("GEMINI_MODEL"), *models])), auto_switch, log)
        self.client = genai.Client(api_key=key)

    def _request(self, model: str, **kw):
        kw.setdefault("generation_config", {"thinking_level": "low"})
        it = self.client.interactions.create(model=model, **kw)
        u = _get(it, "usage")
        tin = (_get(u, "total_input_tokens", 0) or 0) if u is not None else 0
        tout = (_get(u, "total_output_tokens", 0) or 0) if u is not None else 0
        return it, tin, tout

    def _create(self, **kw):
        return super()._create(mid_conversation=bool(kw.get("previous_interaction_id")), **kw)

    def _json(self, system: str, text: str, png: bytes) -> str:
        it = self._create(system_instruction=system, response_mime_type="application/json",
                          input=[{"type": "text", "text": text}, _img(png)])
        return it.output_text or "{}"

    def _act(
        self,
        instruction: str,
        max_turns: int = 15,
        confirm: Callable[[str], bool] = lambda msg: input(f"{msg} [y/N] ").strip().lower() == "y",
        log: Callable[[str], None] = print,
        stop=None,
        narrate: Callable[[str], None] = lambda text: None,
        allow_risky: bool = False,
        confirm_all: bool = False,
    ) -> tuple[bool, str]:
        # Gemini answers on a 1000x1000 grid over the screenshot it last saw; map that onto the
        # display the screenshot came from (which follows the frontmost window).
        png, disp = capture()
        X = lambda v: int(disp[0] + int(v) / 1000 * disp[2])
        Y = lambda v: int(disp[1] + int(v) / 1000 * disp[3])
        tools = [{"type": "computer_use", "environment": "desktop"}, OPEN_APP_TOOL, YIELD_TOOL]
        it = self._create(
            system_instruction=CU_SYSTEM,
            tools=tools,
            input=[{"type": "text", "text": instruction}, _img(png)],
        )
        for _ in range(max_turns):
            if stop is not None and stop.is_set():
                return False, "stopped by user"
            self.check_context()  # the last request's size: start fresh with a summary if it's too big
            calls = [s for s in (_get(it, "steps") or []) if _get(s, "type") == "function_call"]
            if not calls:
                return True, it.output_text or "done"
            results = []
            for call in calls:
                name, args = _get(call, "name"), dict(_get(call, "arguments") or {})
                log(f"  gemini → {name} {json.dumps({k: v for k, v in args.items() if k != 'safety_decision'})}")
                narrate(narration.vision_action(name, args))
                if name == "yield_to_user":
                    return False, f"needs user: {args.get('reason')}"
                if confirm_all and not confirm(f"About to: {name} {json.dumps(args)}. Proceed?"):
                    raise Declined(name)
                extra = {}
                decision = args.pop("safety_decision", None)
                if decision:
                    if decision.get("decision") == "require_confirmation":
                        if not confirm(f"Gemini wants to {name}: {decision.get('explanation')}. Allow?"):
                            return False, "user declined a sensitive action"
                        extra["safety_acknowledgement"] = "true"
                    elif decision.get("decision") == "blocked":
                        return False, f"blocked by safety: {decision.get('explanation')}"
                try:
                    run(name, args, X, Y)
                    out = {"status": "ok", **extra}
                except actions.pyautogui.FailSafeException:
                    raise
                except Exception as e:  # report back so the model can recover
                    out = {"status": "error", "error": str(e), **extra}
                results.append((name, _get(call, "id"), out))
            shot, disp = capture()
            app, _ = ax_elements(limit=1)
            it = self._create(
                previous_interaction_id=it.id,
                system_instruction=CU_SYSTEM,
                tools=tools,
                input=[
                    {
                        "type": "function_result",
                        "name": name,
                        "call_id": cid,
                        "result": [{"type": "text", "text": json.dumps({"frontmost_app": app, **out})}, _img(shot)],
                    }
                    for name, cid, out in results
                ],
            )
        return False, "ran out of turns"


def run(name: str, a: dict, X, Y):
    """Execute one Gemini computer-use action (coordinates on a 1000x1000 grid)."""
    if name in ("click", "double_click", "triple_click", "right_click", "middle_click"):
        button = {"right_click": "right", "middle_click": "middle"}.get(name, "left")
        clicks = {"double_click": 2, "triple_click": 3}.get(name, 1)
        actions.click(X(a["x"]), Y(a["y"]), button=button, clicks=clicks)
    elif name == "move":
        actions.pyautogui.moveTo(X(a["x"]), Y(a["y"]))
    elif name == "mouse_down":
        actions.pyautogui.mouseDown(X(a["x"]), Y(a["y"]))
    elif name == "mouse_up":
        actions.pyautogui.mouseUp(X(a["x"]), Y(a["y"]))
    elif name == "type":
        if "x" in a and "y" in a:
            actions.click(X(a["x"]), Y(a["y"]))
        actions.type_text(a["text"], press_enter=a.get("press_enter", False), clear=a.get("clear_before_typing", False))
    elif name == "press_key":
        actions.hotkey([a["key"]])
    elif name == "hotkey":
        actions.hotkey(a["keys"])
    elif name == "key_down":
        actions.pyautogui.keyDown(actions.key_name(a["key"]))
    elif name == "key_up":
        actions.pyautogui.keyUp(actions.key_name(a["key"]))
    elif name == "scroll":
        actions.scroll(X(a.get("x", 500)), Y(a.get("y", 500)), a.get("direction", "down"), a.get("magnitude_in_pixels", 300))
    elif name == "drag_and_drop":
        actions.drag(X(a["start_x"]), Y(a["start_y"]), X(a["end_x"]), Y(a["end_y"]))
    elif name == "wait":
        actions.time.sleep(float(a.get("seconds", 1)))
    elif name == "open_app":
        if not actions.open_app(a["app_name"]):
            raise RuntimeError(f"no app named {a['app_name']}")
    elif name == "take_screenshot":
        pass
    else:
        raise ValueError(f"unsupported action {name}")
