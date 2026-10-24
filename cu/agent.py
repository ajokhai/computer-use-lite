"""The task loop, in three modes (named by role; the vision model is Claude, OpenAI, Gemini, an
OpenRouter model or a local one, whichever is chosen or available; see cu/vision/):

hybrid  The vision model plans a few atomic steps from a screenshot, Jev grounds each step to
        an accessibility element for a fraction of a cent, the vision model takes any step Jev
        can't place, then looks again and plans the next batch.
jev     Jev alone: each turn it reads the element list and picks the most probable next
        action and its target. No text generation, so it types phrases taken from the task.
vision  The vision model's computer use does everything.
"""

import os
import re
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from . import actions, memory, narrate, recovery, usage
from .config import load_settings
from . import vision
from .jev import Jev, installed_apps, text_candidates
from .screen import Element, ax_elements, bring_forward, frontmost_app
from .vision import Vision
from .vision.base import RISKY_WORDS, is_risky_action  # noqa: F401  (re-exported)

GROUNDED = {"click", "double_click", "type", "scroll"}


def _has_vision() -> bool:
    return vision.configured()


def _has_jev() -> bool:
    return bool(os.environ.get("TYPESAFE_API_KEY") or os.environ.get("JEV_API_KEY"))


def _vision_ok() -> bool:
    return vision.available()


def _jev_ok() -> bool:
    return _has_jev() and usage.available("jev")


MODES = ("auto", "hybrid", "jev", "vision")
MODE_NAMES = {"auto": "Auto", "hybrid": "Vision + Jev", "jev": "Jev Only", "vision": "Vision Only"}


def normalize_mode(mode: str) -> str:
    """Mode names are roles, not vendors; "gemini" is the old name for "vision"."""
    return "vision" if mode == "gemini" else mode


def pick_mode(mode: str = "auto") -> str:
    """Resolve "auto" to the best mode the configured keys and remaining quota allow."""
    mode = normalize_mode(mode)
    if mode != "auto":
        return mode
    seeing, jev = _vision_ok(), _jev_ok()
    if seeing and jev:
        return "hybrid"
    if jev:
        return "jev"
    if seeing:
        return "vision"
    if _has_vision() or _has_jev():
        raise RuntimeError("Every configured model is out of quota right now (see Usage in the menu)")
    raise RuntimeError("Add a Jev and/or vision model key (menu › Settings › API Keys…), or connect an AI app "
                       "(menu › Connect AI Apps) to drive the Mac with any model, no key needed")


def ask_user(msg: str) -> bool:
    if not sys.stdin or not sys.stdin.isatty():
        print(f"{msg} → declined (no one to ask; pass --yolo to allow)")
        return False
    return input(f"{msg} [y/N] ").strip().lower() == "y"


@dataclass
class Agent:
    mode: str = "auto"  # auto | hybrid | jev | vision
    confirm_all: bool = False  # ask before every action
    allow_risky: bool = False  # skip the prompt for steps the planner marks risky
    min_jev_prob: float = 0.55
    max_rounds: int = 12
    confirm: Callable[[str], bool] = ask_user
    log: Callable[[str], None] = print
    narrate: Callable[[str], None] = lambda text: None  # short spoken updates while working
    max_jev_actions: int = 40
    max_attempts: int = recovery.MAX_ATTEMPTS  # tries per task; each failure is assessed before the next
    vision: Vision | None = None
    jev: Jev | None = None
    stats: dict = field(default_factory=lambda: {"jev_steps": 0, "vision_steps": 0, "direct_steps": 0})
    stop: threading.Event = field(default_factory=threading.Event)
    auto_switch: bool | None = None  # None: use the setting

    def __post_init__(self):
        if self.auto_switch is None:
            self.auto_switch = load_settings()["auto_switch"]
        self.mode = pick_mode(self.mode)
        self._clients()

    def _clients(self):
        if self.jev is None and self.mode in ("hybrid", "jev"):
            self.jev = Jev()
        if self.vision is None and (self.mode in ("hybrid", "vision") or _vision_ok()):
            # jev mode can still escalate to the vision model when stuck
            self.vision = Vision(auto_switch=self.auto_switch, log=self.log)

    def _see(self, task: str, max_turns: int) -> tuple[bool, str]:
        return self.vision.act(task, max_turns=max_turns, confirm=self.confirm, log=self.log, stop=self.stop,
                               narrate=self.narrate, allow_risky=self.allow_risky, confirm_all=self.confirm_all)

    def run(self, task: str) -> str:
        """Run the task in up to `max_attempts` attempts: after a failure the agent works out what went
        wrong, tries again differently, and remembers what it learned (cu/recovery.py, cu/memory.py)."""
        lines: deque = deque(maxlen=80)  # recent log lines, for assessing a failed attempt
        log = self.log

        def tap(line):
            lines.append(str(line))
            log(line)

        self.log = tap
        try:
            rec = recovery.Recovery(attempt=self._attempt, log_tail=lambda: list(lines), ask_json=self._ask_json,
                                    where=_frontmost, log=tap, model=self.mode, max_attempts=self.max_attempts)
            _ok, report = rec.run(task)
        finally:
            self.log = log
        return report

    def _attempt(self, task: str) -> tuple[bool, str]:
        self._ok = None
        report = self._run_quota(task)
        ok = self._ok if self._ok is not None else report.lstrip().lower().startswith("done")
        return ok, report

    def _ask_json(self, system: str, text: str) -> dict | None:
        """A vision model's look at a failed attempt, when there is one with quota left."""
        if self.vision is None and not _vision_ok():
            return None
        if self.vision is None:
            self.vision = Vision(auto_switch=self.auto_switch, log=self.log)
        return self.vision.ask_json(system, text)

    def _run_quota(self, task: str) -> str:
        """One attempt, moving to another provider when one runs out of quota (if auto-switch is on)."""
        note = ""
        for _ in range(4):
            try:
                return self._run(task + note)
            except actions.pyautogui.FailSafeException:
                return self._report("stopped: mouse moved into a screen corner")
            except usage.QuotaExhausted as e:
                if e.provider != "jev" and self.mode == "jev":
                    self.log("  the vision model is out of quota; Jev carries on alone")
                    self.vision = None
                    continue
                alt = None
                if self.auto_switch:
                    alt = "vision" if e.provider == "jev" and _vision_ok() else "jev" if e.provider != "jev" and _jev_ok() else None
                if alt is None:
                    back = f" · back in {usage.fmt_until(e.until)}" if e.until else ""
                    return self._report(f"stopped: {e}{back}")
                self.log(f"  {e}; switching to {alt} mode")
                self.narrate(f"{'Jev' if e.provider == 'jev' else 'The vision model'} ran out, switching to {MODE_NAMES[alt]}")
                self.mode = alt
                self._clients()
                note = "\n(Continue from the current screen; some of this may already be done.)"
        return self._report("stopped: out of quota everywhere")

    def _run(self, task: str) -> str:
        if self.mode == "jev":
            return self.run_jev(task)
        if self.mode == "vision":
            self._ok, msg = self._see(task, max_turns=40)
            return self._report(msg)

        history: list[str] = []
        for round_ in range(self.max_rounds):
            if self.stop.is_set():
                return self._report("stopped by user")
            plan = self.vision.plan(task, history)
            status, steps = plan.get("status", "continue"), plan.get("steps") or []
            self.log(f"[plan {round_ + 1}] {status}: {plan.get('summary', '')}")
            if status == "continue" and plan.get("summary"):
                self.narrate(plan["summary"])
            if status == "done" or (status != "blocked" and not steps):
                self._ok = status == "done"
                return self._report(plan.get("summary", "done"))
            if status == "blocked":
                return self._report(f"blocked: {plan.get('summary')}")
            for step in steps:
                if self.stop.is_set():
                    return self._report("stopped by user")
                ok, note = self.step(step)
                history.append(f"{'ok' if ok else 'FAILED'}: {_fmt(step)} {note}".strip())
                if not ok:
                    break  # replan from what's on screen now
        return self._report("stopped: hit the round limit")

    def verify_done(self, task: str, app: str, els: list[Element], history: list[str]) -> tuple[bool, str]:
        """Ask Jev whether the task is achieved, from the screen and what was done. No keyword rules:
        the model judges, and its probability is the answer."""
        if not history:
            return False, "nothing has been done yet"
        if self.jev is None or not els:
            return True, "no verifier available"
        p = self.jev.check(task, app, els, history=history)
        return p >= 0.5, f"jev check p={p:.2f}"

    def run_jev(self, task: str) -> str:
        """Jev picks op + target + text + app in one request per turn, from the AX tree."""
        hints = [f"NOTE: {n}" for n in recovery.notes_of(task)]  # lessons and retry guidance, as history Jev reads
        self._hint_urls = URL.findall("\n".join(hints))  # direct pages that lessons and skills point to
        task = recovery.strip_notes(task)
        texts, apps = text_candidates(task) + self._hint_urls, installed_apps()
        history: list[str] = []
        recent: deque = deque(maxlen=3)
        seen_screens: dict[str, list[str]] = {}
        misses = 0
        for _ in range(self.max_jev_actions):
            if self.stop.is_set():
                return self._report("stopped by user")
            app, els = ax_elements(limit=253)

            # Screen signature for tracking actions tried on this exact screen
            screen_sig = f"{app}:" + ",".join(f"{e.role}:{e.label[:12]}:{e.x // 25}:{e.y // 25}" for e in els[:15])
            tried_here = seen_screens.get(screen_sig, [])

            d = self.jev.decide(task, app, els, hints + history, texts, apps, tried=tried_here)
            op, el = d["op"], d["element"]
            desc = _describe_op(d)
            self.log(f"  jev → {desc}  p={d['op_p']:.2f}")
            self.narrate(narrate.jev_op(op, el.label if el else "", d["text"], d["app"]))

            if op == "DONE":
                ok_verified, why = self.verify_done(task, app, els, history)
                if not ok_verified:
                    self.log(f"  not done yet ({why})")
                    self._failed(history, f"FAILED: DONE was premature ({why}); finish the remaining steps")
                    seen_screens.setdefault(screen_sig, []).append("DONE")
                    misses = 0
                    continue
                return self._report(f"done (verified; jev p={d['op_p']:.2f})")

            # Stuck: Jev says so, keeps missing, or repeats the same action three times.
            stuck = op == "BLOCKED" or misses >= 3 or (len(recent) == 3 and len(set(recent)) == 1)
            if stuck or (self.mode != "jev" and self.vision and d["op_p"] < 0.35):
                if self.mode == "jev" or not self.vision or not _vision_ok():
                    ok_verified, why = self.verify_done(task, app, els, history)
                    if ok_verified and history:
                        return self._report(f"done ({why})")
                    hint = "" if _has_vision() else "; add a vision model key so it can take over"
                    return self._report(f"blocked: Jev is stuck at '{desc}'{hint}")
                self.stats["vision_steps"] += 1
                ok, msg = self._see(f"Task: {task}\nDone so far: {history[-8:]}\nDo the next step or two, then stop.", 6)
                history.append(f"vision model: {msg}")
                recent.clear()
                misses = 0
                continue
            if op in ("CLICK", "DOUBLE_CLICK", "TYPE") and el is None and not (op == "TYPE" and _focused(els)):
                # Not in the accessibility tree: ask Jev's vision to find it on a screenshot. For typing,
                # look for the text box, not the words to be typed.
                target_desc = (f'text box to type "{d["text"][:40]}" into' if op == "TYPE" else d.get("text") or task)
                pt, conf = self.jev.locate(target_desc) if self.jev else (None, 0.0)
                if pt and conf >= 0.2:
                    cx, cy, cw, ch = pt
                    el = Element(id="visual_jev", role="AXVisual", label=target_desc, x=cx, y=cy, w=cw, h=ch)
                    self.log(f"    jev box visual grounding → at ({cx}, {cy}) conf={conf:.2f}")
                else:
                    misses += 1
                    self._failed(history, f"FAILED: {desc} (no matching element)")
                    seen_screens.setdefault(screen_sig, []).append(desc)
                    continue

            label = (el.label if el else "").lower()
            if self.confirm_all or (op in ("CLICK", "DOUBLE_CLICK", "PRESS_ENTER") and not self.allow_risky
                                    and is_risky_action(op, label, desc, task)):
                if not self.confirm(f"About to: {desc}. Proceed?"):
                    return self._report("stopped: user declined")
            try:
                self._do_jev(op, d, el, task, els)
            except actions.pyautogui.FailSafeException:
                raise
            except Exception as e:
                self._failed(history, f"FAILED: {desc} ({e})")
                seen_screens.setdefault(screen_sig, []).append(desc)
                misses += 1
                continue
            self.stats["jev_steps"] += 1
            history.append(f"ok: {desc}")
            seen_screens.setdefault(screen_sig, []).append(desc)
            recent.append(desc)
            time.sleep(0.08)
        return self._report("stopped: hit the action limit")

    def _failed(self, history: list[str], why: str):
        """Note a failed step for Jev's next choice and in the log (which recovery reads to assess)."""
        history.append(why)
        self.log(f"    {why}")

    def _do_jev(self, op: str, d: dict, el: Element | None, task: str = "", els: list[Element] | None = None):
        if els is None:
            els = []
        if op in ("CLICK", "DOUBLE_CLICK"):
            bring_forward(el)
            actions.click(el.x, el.y, clicks=2 if op == "DOUBLE_CLICK" else 1)
        elif op in ("TYPE", "PASTE"):
            self._focus_text_field(el)
            if op == "TYPE":
                actions.type_text(d["text"], clear=el is not None and el.role in ("AXTextField", "AXSearchField"))
            else:
                actions.paste()
        elif op == "COPY":
            actions.copy_selection()
        elif op == "SELECT_ALL":
            actions.select_all()
        elif op == "CLOSE_WINDOW":
            actions.close_window()
        elif op == "QUIT_APP":
            actions.quit_app(d.get("app"))
        elif op == "SCREENSHOT":
            # If an AXWindow or card is focused/present, capture cropped window
            win = next((e for e in els if e.role == "AXWindow" and e.w > 100 and e.h > 100), None)
            if win:
                rect = (win.x - win.w // 2, win.y - win.h // 2, win.w, win.h)
                actions.screenshot_to_clipboard(rect=rect)
            else:
                actions.screenshot_to_clipboard()
        elif op == "PRESS_ENTER":
            actions.hotkey(["enter"])
        elif op == "PRESS_ESCAPE":
            actions.hotkey(["escape"])
        elif op == "PRESS_TAB":
            actions.hotkey(["tab"])
        elif op == "NEW_ITEM":
            actions.create_new_document(d.get("app"))
        elif op in ("SCROLL_DOWN", "SCROLL_UP"):
            x, y = (el.x, el.y) if el else (None, None)
            if x is None:
                from .screen import active_display
                dx, dy, w, h = active_display()
                x, y = dx + w // 2, dy + h // 2
            actions.scroll(x, y, "down" if op == "SCROLL_DOWN" else "up", 400)
        elif op == "OPEN_APP":
            dest = _destination(task, getattr(self, "_hint_urls", []))
            is_browser = d.get("app", "").lower() in ("safari", "google chrome", "chrome", "arc", "brave", "firefox", "edge")
            if dest and is_browser:
                actions.open_url(dest, browser=d.get("app"))
            else:
                if not actions.open_app(d["app"]):
                    raise RuntimeError(f"could not open {d['app']}")
        elif op == "WAIT":
            time.sleep(0.5)

    def _focus_text_field(self, el: Element | None):
        """Make sure a text field has focus before typing or pasting. Typing anywhere else can
        trigger single-key shortcuts (on X, "l" likes and "t" reposts), so with no text field
        nothing is typed and the step fails, letting the model choose again."""
        if el is not None and el.role not in TEXT_INPUT and el.role != "AXVisual":
            raise RuntimeError(f"{el.describe()} isn't a text field")
        if el is not None and not el.focused:
            bring_forward(el)
            actions.click(el.x, el.y)
            time.sleep(0.15)
        if not _focused(ax_elements(limit=253)[1]):
            raise RuntimeError("no text field has focus, so nothing was typed")

    def step(self, s: dict) -> tuple[bool, str]:
        kind = s.get("kind")
        self.log(f"  • {_fmt(s)}")
        self.narrate(narrate.step(s))
        if (s.get("risky") and not self.allow_risky) or self.confirm_all:
            if not self.confirm(f"About to: {_fmt(s)}. Proceed?"):
                return False, "(user declined)"

        try:
            if kind == "open_app":
                self.stats["direct_steps"] += 1
                return actions.open_app(s["app"]), ""
            if kind == "open_url":
                self.stats["direct_steps"] += 1
                actions.open_url(s["url"])
                return True, ""
            if kind == "hotkey":
                self.stats["direct_steps"] += 1
                actions.hotkey(s["keys"])
                time.sleep(0.4)
                return self._verify(s)
            if kind == "wait":
                time.sleep(float(s.get("seconds", 1)))
                return True, ""
            if kind == "type" and not s.get("target"):
                self.stats["direct_steps"] += 1
                actions.type_text(s["text"], press_enter=s.get("enter", False))
                time.sleep(0.3)
                return self._verify(s)
            if kind in GROUNDED:
                return self._grounded(s)
        except (usage.QuotaExhausted, actions.pyautogui.FailSafeException):
            raise
        except Exception as e:
            return False, f"({e})"
        return self._fallback(s, f"unknown step kind {kind}")

    def _grounded(self, s: dict) -> tuple[bool, str]:
        app, els = ax_elements(limit=254)
        el, prob = self.jev.ground(s, app, els) if els else (None, 0.0)
        if el is None or prob < self.min_jev_prob:
            why = f"jev unsure ({prob:.2f})" if els else "no accessibility elements"
            return self._fallback(s, why)

        self.log(f"    jev → {el.describe()} p={prob:.2f}")
        self.stats["jev_steps"] += 1
        kind = s["kind"]
        bring_forward(el)  # a window covering it would take the click
        if kind in ("click", "double_click"):
            actions.click(el.x, el.y, clicks=2 if kind == "double_click" else 1)
        elif kind == "type":
            actions.click(el.x, el.y)
            actions.type_text(s["text"], press_enter=s.get("enter", False), clear=el.role != "AXTextArea")
        elif kind == "scroll":
            actions.scroll(el.x, el.y, s.get("direction", "down"))
        time.sleep(0.5)
        return self._verify(s)

    def _verify(self, s: dict) -> tuple[bool, str]:
        """Cheap post-check with Jev against the AX tree; the vision model replans if it fails."""
        if not s.get("expect") or self.jev is None:
            return True, ""
        app, els = ax_elements(limit=200)
        p = self.jev.check(s["expect"], app, els) if els else 1.0
        return (p >= 0.4), f"(expect p={p:.2f})"

    def _fallback(self, s: dict, why: str) -> tuple[bool, str]:
        self.log(f"    {why} → vision model")
        self.stats["vision_steps"] += 1
        ok, msg = self._see(f"Do exactly this one step, then stop: {_fmt(s)}", 6)
        return ok, f"({msg})"

    def _report(self, msg: str) -> str:
        try:
            from . import indicator
            indicator.hide_cursor_active(delay=0.1)
        except Exception:
            pass
        lines = [msg, f"mode: {self.mode}, steps: {self.stats}"]
        if self.vision:
            lines += self.vision.report()
        if self.jev:
            lines.append(f"jev: {self.jev.input_tokens} tokens ≈ ${self.jev.cost_usd:.5f}")
        out = "\n".join(lines)
        self.log(out)
        return out


def _fmt(s: dict) -> str:
    k = s.get("kind")
    detail = s.get("target") or s.get("app") or s.get("url") or "+".join(s.get("keys", [])) or ""
    if k == "type":
        detail = f'"{s.get("text", "")}" into {s.get("target") or "focused field"}'
    return f"{k} {detail}".strip() + (f" — {s['do']}" if s.get("do") else "")


TEXT_INPUT = ("AXTextField", "AXTextArea", "AXSearchField", "AXComboBox")


URL = re.compile(r'https?://[^\s,;"\'()]+')
SHARED = re.compile(r"(include|add|attach|share|paste|with|containing)\s+(the\s+|this\s+|a\s+)?(link|url)\W*$", re.I)


def _destination(task: str, hint_urls: list[str]) -> str | None:
    """Where to point the browser: a page lessons or skills name for a site in the task, else a URL the
    task says to go to, else the site itself. Never a link the task wants shared (\"include the link …\")."""
    places = memory.places(task)
    for u in hint_urls:
        if any(p in u for p in places):
            return u.rstrip(".")
    shared = {m.group(0).rstrip(".") for m in URL.finditer(task) if SHARED.search(task[: m.start()][-40:])}
    unquoted = memory.QUOTED.sub(" ", task)  # links inside quoted text are content, not places to go
    for m in URL.finditer(unquoted):
        if m.group(0).rstrip(".") not in shared:
            return m.group(0).rstrip(".")
    return f"https://{places[0]}" if places else None


def _frontmost() -> str:
    try:
        return frontmost_app()[0]
    except Exception:
        return ""


def _focused(els: list[Element]) -> bool:
    return any(e.focused and e.role in TEXT_INPUT for e in els)


def _describe_op(d: dict) -> str:
    op, el = d["op"], d["element"]
    if op == "TYPE":
        return f'TYPE "{d["text"]}" into {el.describe() if el else "focused field"}'
    if op == "OPEN_APP":
        return f"OPEN_APP {d['app']}"
    if op == "CLOSE_WINDOW":
        return "CLOSE_WINDOW"
    if op == "QUIT_APP":
        return f"QUIT_APP {d.get('app', '')}".strip()
    if op == "COPY":
        return "COPY selection"
    if op == "SELECT_ALL":
        return "SELECT_ALL"
    return f"{op} {el.describe()}" if el and op in ("CLICK", "DOUBLE_CLICK", "SCROLL_DOWN", "SCROLL_UP") else op
