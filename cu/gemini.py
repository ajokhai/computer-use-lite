"""Gemini: plans tasks into atomic steps, and drives the desktop by vision when Jev can't."""

import base64
import json
import os
from typing import Callable

from google import genai

from . import actions, narrate as narration, usage
from .config import load_settings
from .screen import ax_elements, screen_size, screenshot_png

PLAN_SYSTEM = """You control a macOS computer for the user. Look at the screenshot and the
accessibility elements of the frontmost app, then return the next few ATOMIC steps toward the task.

Return JSON: {"status": "continue"|"done"|"blocked", "summary": str, "steps": [step, ...]}
Each step is one of:
  {"kind": "open_app", "app": "Safari"}
  {"kind": "open_url", "url": "https://..."}
  {"kind": "click", "target": "<exact label/role of the element>", "do": "<why>"}
  {"kind": "double_click", "target": "...", "do": "..."}
  {"kind": "type", "target": "<text field label, or empty for the focused field>", "text": "<literal text>", "enter": bool, "do": "..."}
  {"kind": "hotkey", "keys": ["command", "t"], "do": "..."}
  {"kind": "scroll", "direction": "up"|"down", "target": "<area>", "do": "..."}
  {"kind": "wait", "seconds": 1}
Optional on any step: "expect": "<short fact that should be true after it>",
"risky": true when it sends, buys, deletes, posts or submits something irreversible.

Rules: give at most 6 steps, stop the list where the screen will change in ways you can't
predict. Use target names exactly as they appear in the element list when possible.
Prefer keyboard shortcuts and open_app/open_url over clicking through UI.
status "done" with no steps when the task is complete; "blocked" if it needs the user."""

CU_SYSTEM = """You operate a macOS desktop. Complete the instruction with the fewest actions,
then stop calling tools and reply with one sentence saying what you did. Use open_app to
launch applications. Call yield_to_user if you need credentials, 2FA, or a decision."""

OPEN_APP_TOOL = {
    "type": "function",
    "name": "open_app",
    "description": "Launch or focus a macOS application by name.",
    "parameters": {"type": "object", "properties": {"app_name": {"type": "string"}}, "required": ["app_name"]},
}
YIELD_TOOL = {
    "type": "function",
    "name": "yield_to_user",
    "description": "Hand control back to the user when an action is unsafe, ambiguous, or needs credentials.",
    "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]},
}


def _img(png: bytes) -> dict:
    return {"type": "image", "data": base64.b64encode(png).decode(), "mime_type": "image/png"}


def _get(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


class ModelSwitched(Exception):
    """Quota ran out mid-conversation; the caller restarts from the current screen on the new model."""


class Gemini:
    def __init__(self, api_key: str | None = None, auto_switch: bool | None = None, log: Callable[[str], None] = print):
        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError("Set GEMINI_API_KEY to use Gemini")
        settings = load_settings()
        self.client = genai.Client(api_key=key)
        self.models = list(dict.fromkeys(filter(None, [os.environ.get("GEMINI_MODEL"), *settings["gemini_models"]])))
        self.auto_switch = settings["auto_switch"] if auto_switch is None else auto_switch
        self.log = log
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    @property
    def model(self) -> str:
        """First model with quota left (or just the first one when switching is off)."""
        candidates = self.models if self.auto_switch else self.models[:1]
        for m in candidates:
            if usage.available(m):
                return m
        s = usage.status(candidates[0])
        raise usage.QuotaExhausted("gemini", s["blocked_until"], "all Gemini models")

    def available(self) -> bool:
        try:
            self.model
            return True
        except usage.QuotaExhausted:
            return False

    def _create(self, **kw):
        kw.setdefault("generation_config", {"thinking_level": "low"})
        tried = set()
        while True:
            model = self.model
            if model in tried:
                s = usage.status(model)
                raise usage.QuotaExhausted("gemini", s.get("blocked_until"), f"all Gemini models out of quota ({usage.pretty(model)})")
            tried.add(model)
            try:
                it = self.client.interactions.create(model=model, **kw)
                break
            except Exception as e:
                quota, until = usage.is_quota_error(e)
                if not quota:
                    raise
                usage.mark_exhausted(model, until)
                self.log(f"  {usage.pretty(model)} is out of quota")
                nxt = self.model  # raises QuotaExhausted when nothing is left
                self.log(f"  switching to {usage.pretty(nxt)}")
                if kw.get("previous_interaction_id"):
                    raise ModelSwitched(nxt) from e
        self.calls += 1
        u = _get(it, "usage")
        tin = (_get(u, "total_input_tokens", 0) or 0) if u is not None else 0
        tout = (_get(u, "total_output_tokens", 0) or 0) if u is not None else 0
        self.input_tokens += tin
        self.output_tokens += tout
        usage.record(model, tin, tout)
        return it

    def plan(self, task: str, history: list[str]) -> dict:
        app, els = ax_elements(limit=150)
        context = {
            "task": task,
            "frontmost_app": app,
            "elements": [e.describe() for e in els],
            "done_so_far": history[-30:],
        }
        it = self._create(
            system_instruction=PLAN_SYSTEM,
            response_mime_type="application/json",
            input=[{"type": "text", "text": json.dumps(context)}, _img(screenshot_png())],
        )
        text = it.output_text or "{}"
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            return json.loads(text[start : end + 1])

    def act(self, instruction: str, max_turns: int = 15, **kw) -> tuple[bool, str]:
        """Vision computer-use loop. Returns (success, final message)."""
        for _ in range(len(self.models)):
            try:
                return self._act(instruction, max_turns, **kw)
            except ModelSwitched:
                instruction = f"{instruction}\n(Continue from the current screen; some of this may already be done.)"
        return False, "kept running out of quota"

    def _act(
        self,
        instruction: str,
        max_turns: int = 15,
        confirm: Callable[[str], bool] = lambda msg: input(f"{msg} [y/N] ").strip().lower() == "y",
        log: Callable[[str], None] = print,
        stop=None,
        narrate: Callable[[str], None] = lambda text: None,
    ) -> tuple[bool, str]:
        sw, sh = screen_size()
        X = lambda v: int(int(v) / 1000 * sw)
        Y = lambda v: int(int(v) / 1000 * sh)
        tools = [{"type": "computer_use", "environment": "desktop"}, OPEN_APP_TOOL, YIELD_TOOL]
        it = self._create(
            system_instruction=CU_SYSTEM,
            tools=tools,
            input=[{"type": "text", "text": instruction}, _img(screenshot_png())],
        )
        for _ in range(max_turns):
            if stop is not None and stop.is_set():
                return False, "stopped by user"
            calls = [s for s in (_get(it, "steps") or []) if _get(s, "type") == "function_call"]
            if not calls:
                return True, it.output_text or "done"
            results = []
            for call in calls:
                name, args = _get(call, "name"), dict(_get(call, "arguments") or {})
                log(f"  gemini → {name} {json.dumps({k: v for k, v in args.items() if k != 'safety_decision'})}")
                narrate(narration.gemini_action(name, args))
                if name == "yield_to_user":
                    return False, f"needs user: {args.get('reason')}"
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
                except Exception as e:  # report back so the model can recover
                    out = {"status": "error", "error": str(e), **extra}
                results.append((name, _get(call, "id"), out))
            shot = screenshot_png()
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
