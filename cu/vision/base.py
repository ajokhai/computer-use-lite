"""What every vision model shares: the planning prompt, model stepping on quota, coordinate
scaling between a screenshot and screen points, and asking before risky clicks and keys."""

import base64
import io
import json
import re
import time
from collections import deque
from contextlib import contextmanager
from typing import Callable

from PIL import Image

from .. import actions, usage
from ..screen import Element, ax_elements, capture, screenshot_png

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
launch applications. Keyboard shortcuts use cmd (not ctrl) on macOS. Call yield_to_user if you
need credentials, 2FA, or a decision."""

CONTINUE_NOTE = "\n(Continue from the current screen; some of this may already be done.)"

# The only things worth interrupting the user for: spending money and permanently deleting, and
# only when their request didn't ask for it ("post a tweet" means clicking Post needs no question).
# Control label word -> phrases in a request that ask for it.
RISKY_WORDS = {
    "buy": ("buy", "purchas", "order", "pay"),
    "pay": ("pay", "purchas", "buy", "checkout", "check out", "order"),
    "purchase": ("purchas", "buy", "order"),
    "checkout": ("checkout", "check out", "buy", "purchas", "order", "pay"),
    "place order": ("order", "buy", "purchas"),
    "subscribe": ("subscri",),
    "transfer": ("transfer", "send money", "pay"),
    "send money": ("send money", "pay", "transfer"),
    "donate": ("donat",),
    "delete": ("delet", "remov", "clean up", "clear"),
    "erase": ("eras", "wipe", "delet"),
    "empty trash": ("empty", "trash", "delet"),
}


def is_risky_action(op: str, label: str = "", detail: str = "", task: str = "") -> bool:
    """Whether an action needs the user's OK: it pays or permanently deletes, judged by the
    control's label (whole words), and the request in `task` didn't ask for that."""
    text, asked = f"{label} {detail}".lower(), task.lower()
    return any(re.search(rf"\b{re.escape(word)}\b", text) and not any(a in asked for a in asks)
               for word, asks in RISKY_WORDS.items())


# A comfortable working size for one conversation, in input tokens, per model (by id prefix). These
# sit well under each model's hard limit, because long runs full of screenshots get worse before they
# hit it. Past CONTEXT_FILL of the budget the conversation starts over with a summary of what's done.
# The "context_budgets" setting ({"<model id or prefix>": tokens}) overrides them.
CONTEXT_BUDGETS = [("claude-", 200_000), ("gemini-", 200_000), ("gpt-", 128_000), ("openrouter:", 64_000),
                   ("local:", 16_000)]
DEFAULT_BUDGET = 64_000
CONTEXT_FILL = 0.8
MAX_FRESH_STARTS = 3


def context_budget(model: str) -> int:
    from ..config import load_settings
    over = load_settings().get("context_budgets") or {}
    for table in (sorted(over.items(), key=lambda kv: -len(kv[0])), CONTEXT_BUDGETS):
        for prefix, tokens in table:
            if model == prefix or model.startswith(prefix):
                return int(tokens)
    return DEFAULT_BUDGET


class ContextFull(Exception):
    """The conversation outgrew the model's budget; the caller starts a fresh one with a summary."""


class ModelSwitched(Exception):
    """Quota ran out mid-conversation; the caller restarts from the current screen on the new model."""


class Declined(Exception):
    """The user said no to an action; the task stops."""


def parse_json(text: str) -> dict:
    text = text or "{}"
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        return json.loads(text[start : end + 1]) if start >= 0 < end else {}


def b64(png: bytes) -> str:
    return base64.b64encode(png).decode()


def data_url(png: bytes) -> str:
    return f"data:image/png;base64,{b64(png)}"


class Shot:
    """One screenshot of the display in use, with the mapping from its pixels to global screen
    points: the display's origin (other displays can sit at negative coordinates) and the
    scale (Retina and downscaling)."""

    ox = oy = 0  # the display's top-left corner in global points

    def __init__(self, max_width: int = 1280):
        self.png, (self.ox, self.oy, w, _h) = capture(max_width=max_width)
        self.width, self.height = Image.open(io.BytesIO(self.png)).size
        self.scale = w / self.width

    def point(self, x, y) -> tuple[int, int]:
        """Screenshot pixels → global screen points."""
        return int(round(self.ox + float(x) * self.scale)), int(round(self.oy + float(y) * self.scale))

    def pixel(self, x, y) -> tuple[int, int]:
        """Global screen points → screenshot pixels."""
        return int(round((float(x) - self.ox) / self.scale)), int(round((float(y) - self.oy) / self.scale))


def element_at(x: int, y: int) -> Element | None:
    """The smallest accessibility element containing the point, so a click can be judged by its label."""
    _, els = ax_elements(limit=200)
    hits = [e for e in els if abs(e.x - x) <= e.w / 2 and abs(e.y - y) <= e.h / 2]
    return min(hits, key=lambda e: e.w * e.h) if hits else None


class Guard:
    """Asks before an action only when the user wants every action confirmed, or when it would pay
    or permanently delete something the request didn't ask for (judged by the accessibility label
    under the point, or of the field Return is pressed in). The same rule the Jev loop uses."""

    def __init__(self, confirm: Callable[[str], bool], allow_risky: bool = False, confirm_all: bool = False,
                 task: str = ""):
        self.confirm, self.allow_risky, self.confirm_all, self.task = confirm, allow_risky, confirm_all, task

    def _ask(self, what: str):
        if not self.confirm(f"About to: {what}. Proceed?"):
            raise Declined(what)

    def click(self, x: int, y: int, what: str = "click"):
        if self.confirm_all:
            return self._ask(what)
        if self.allow_risky:
            return
        el = element_at(x, y)
        if el is not None and is_risky_action("CLICK", el.label, what, self.task):
            self._ask(f"{what} {el.describe()}")

    def keys(self, keys: list[str], what: str = ""):
        what = what or "press " + "+".join(keys)
        if self.confirm_all:
            return self._ask(what)
        if self.allow_risky or not any(actions.key_name(k) == "enter" for k in keys):
            return
        _, els = ax_elements(limit=200)
        el = next((e for e in els if e.focused), None)
        if el is not None and is_risky_action("PRESS_ENTER", el.label, what, self.task):
            self._ask(f"{what} in {el.describe()}")

    def other(self, what: str):
        if self.confirm_all:
            self._ask(what)


@contextmanager
def holding(mods: list[str] | str | None):
    """Hold modifier keys (e.g. "shift", "cmd+shift", ["shift"]) around a mouse action."""
    if isinstance(mods, str):
        mods = [m for m in mods.replace(" ", "").split("+") if m]
    keys = [actions.key_name(m) for m in mods or []]
    for k in keys:
        actions.pyautogui.keyDown(k)
    try:
        yield
    finally:
        for k in reversed(keys):
            actions.pyautogui.keyUp(k)


def sleep(seconds, stop=None, cap: float = 10.0):
    """Wait, but no longer than `cap`, and wake early when the user stops the task."""
    end = time.time() + min(max(float(seconds or 0), 0.0), cap)
    while time.time() < end:
        if stop is not None and stop.is_set():
            return
        time.sleep(min(0.1, end - time.time()))


class VisionModel:
    """One provider (one SDK client) and its list of models, tried in order as quota runs out.

    Subclasses implement `_request(model, **kw)` (one API call, returning (response, in, out)),
    `_json(system, text, png)` for planning, and `_act(...)` for vision computer use."""

    provider = ""
    label = ""

    def __init__(self, models: list[str], auto_switch: bool = True, log: Callable[[str], None] = print):
        if not models:
            raise RuntimeError(f"No {self.label} model is set")
        self.models = list(dict.fromkeys(models))
        self.auto_switch = auto_switch
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
        raise usage.QuotaExhausted(self.provider, s["blocked_until"], f"all {self.label} models")

    def available(self) -> bool:
        try:
            self.model
            return True
        except usage.QuotaExhausted:
            return False

    def unreachable(self, err: Exception) -> bool:
        """A connection error that should move on to another model (a local server that isn't running)."""
        return False

    def _create(self, mid_conversation: bool = False, **kw):
        """One request on the current model; on a quota error, mark it out and try the next one."""
        tried = set()
        while True:
            model = self.model
            if model in tried:
                s = usage.status(model)
                raise usage.QuotaExhausted(self.provider, s.get("blocked_until"),
                                           f"all {self.label} models out of quota ({usage.pretty(model)})")
            tried.add(model)
            try:
                resp, tin, tout = self._request(model, **kw)
                break
            except Exception as e:
                quota, until = usage.is_quota_error(e)
                if self.unreachable(e):
                    quota, until = True, time.time() + 60
                if not quota:
                    raise
                usage.mark_exhausted(model, until)
                self.log(f"  {usage.pretty(model)} is out of quota" if not self.unreachable(e)
                         else f"  {usage.pretty(model)} isn't reachable")
                nxt = self.model  # raises QuotaExhausted when nothing is left
                self.log(f"  switching to {usage.pretty(nxt)}")
                if mid_conversation:
                    raise ModelSwitched(nxt) from e
        self.calls += 1
        self.last_input, self.last_model = tin, model
        self.input_tokens += tin
        self.output_tokens += tout
        usage.record(model, tin, tout)
        return resp

    def _request(self, model: str, **kw):
        raise NotImplementedError

    def _json(self, system: str, text: str, png: bytes) -> str:
        raise NotImplementedError

    def plan(self, task: str, history: list[str]) -> dict:
        app, els = ax_elements(limit=150)
        context = {
            "task": task,
            "frontmost_app": app,
            "elements": [e.describe() for e in els],
            "done_so_far": history[-30:],
        }
        return parse_json(self._json(PLAN_SYSTEM, json.dumps(context), screenshot_png()))

    def check_context(self):
        """Raise ContextFull once the last request used most of this model's context budget."""
        used, model = getattr(self, "last_input", 0), getattr(self, "last_model", "")
        if model and used > CONTEXT_FILL * context_budget(model):
            raise ContextFull(f"{used} input tokens on {model}")

    def act(self, instruction: str, max_turns: int = 15, **kw) -> tuple[bool, str]:
        """Vision computer-use loop. Returns (success, final message). Starts a fresh conversation,
        with a summary of what was done, when the current one gets too long for the model."""
        trail: deque = deque(maxlen=25)  # recent actions, for the summary a fresh start begins from
        log = kw.get("log") or print

        def tap(line):
            trail.append(str(line).strip())
            log(line)
        kw["log"] = tap
        task, switches, fresh = instruction, 0, 0
        while True:
            try:
                return self._act(instruction, max_turns, **kw)
            except ModelSwitched:
                switches += 1
                if switches >= len(self.models):
                    return False, "kept running out of quota"
                instruction += CONTINUE_NOTE
            except ContextFull as e:
                fresh += 1
                if fresh > MAX_FRESH_STARTS:
                    return False, "the task needed more than the model's context allows"
                tap(f"  context is getting long ({e}); starting a fresh conversation with a summary")
                done = "\n".join(f"- {ln}" for ln in list(trail)[-20:] if "→" in ln) or "- (no actions logged)"
                instruction = f"{task}\n(Fresh start: the earlier conversation got too long. Done so far:\n{done}" \
                              f"{CONTINUE_NOTE.lstrip()})"
                self.last_input = 0
            except Declined as d:
                return False, f"user declined: {d}"

    def _act(self, instruction: str, max_turns: int = 15, confirm: Callable[[str], bool] = lambda m: False,
             log: Callable[[str], None] = print, stop=None, narrate: Callable[[str], None] = lambda t: None,
             allow_risky: bool = False, confirm_all: bool = False) -> tuple[bool, str]:
        raise NotImplementedError


OPEN_APP_SCHEMA = {"type": "object", "properties": {"app_name": {"type": "string"}}, "required": ["app_name"]}
YIELD_SCHEMA = {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}
OPEN_APP_DESC = "Launch or focus a macOS application by name."
YIELD_DESC = "Hand control back to the user when an action is unsafe, ambiguous, or needs credentials."


def open_app(name: str):
    if not actions.open_app(name):
        raise RuntimeError(f"no app named {name}")
