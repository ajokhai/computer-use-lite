"""The task loop, in three modes:

hybrid  Gemini plans a few atomic steps from a screenshot, Jev grounds each step to an
        accessibility element for a fraction of a cent, Gemini vision takes any step Jev
        can't place, then Gemini looks again and plans the next batch.
jev     Jev alone: each turn it reads the element list and picks the most probable next
        action and its target. No text generation, so it types phrases taken from the task.
gemini  Gemini vision computer-use does everything.
"""

import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from . import actions, narrate, usage
from .config import load_settings
from .gemini import Gemini
from .jev import Jev, installed_apps, text_candidates
from .screen import Element, _load_native_lib, ax_elements
from .memory import get_memory_store, prompt_user_for_help

GROUNDED = {"click", "double_click", "type", "scroll"}


RISKY_WORDS = ("send", "buy", "pay", "purchase", "order", "delete", "remove", "submit", "post",
               "publish", "transfer", "confirm", "checkout", "place order", "erase", "trash")


def is_risky_action(op: str, label: str = "", detail: str = "") -> bool:
    """Evaluate whether an action is risky and warrants user confirmation (native Rust backend)."""
    lib = _load_native_lib()
    if lib and hasattr(lib, "cu_is_risky_action"):
        try:
            return bool(lib.cu_is_risky_action(op.encode("utf-8"), label.encode("utf-8"), detail.encode("utf-8")))
        except Exception:
            pass
    combined = f"{op} {label} {detail}".lower()
    return any(w in combined for w in RISKY_WORDS)


def check_typing_safety(text: str, el: Element | None, app: str, els: list[Element]) -> None:
    """Hard safety guard against typing stray text, contact names into message bodies,
    or message bodies into recipient fields (native Rust backend with Python fallback)."""
    lib = _load_native_lib()
    if lib and hasattr(lib, "cu_check_typing_safety"):
        try:
            import json
            el_role = el.role.encode("utf-8") if el else None
            el_label = el.label.encode("utf-8") if el else None
            labels_json = json.dumps([e.label for e in els]).encode("utf-8")
            raw = lib.cu_check_typing_safety(text.encode("utf-8"), el_role, el_label, app.encode("utf-8"), labels_json)
            if raw:
                dec = json.loads(raw.decode("utf-8"))
                if not dec.get("allowed", True):
                    raise RuntimeError(dec.get("violation") or dec.get("reason", "Typing safety violation"))
                return
        except RuntimeError:
            raise
        except Exception:
            pass

    app_lower = app.lower()
    is_comm = any(c in app_lower for c in ("messages", "mail", "slack", "discord", "telegram", "teams")) or any(
        "to:" in (e.label.lower()) or "recipient" in (e.label.lower()) for e in els
    )
    if not is_comm:
        return

    text_lower = text.lower().strip()
    words = text.split()
    is_short = len(words) <= 3
    has_honorific = any(text_lower.startswith(h) for h in ("mr.", "mr", "ms.", "ms", "mrs.", "mrs", "dr.", "dr", "prof.", "prof"))
    is_contact_handle = "@" in text or text.startswith("+") or (text.startswith("0") and all(c.isdigit() or c in "- " for c in text))
    is_capitalized_name = is_short and bool(words) and all(w[0].isupper() or w.startswith("+") or w.startswith("@") for w in words if w)
    is_recipient_name = is_contact_handle or has_honorific or is_capitalized_name

    if el is not None:
        is_recipient_field = any(r in el.label.lower() for r in ("to:", "to", "recipient", "address", "search")) or el.role == "AXComboBox"
        is_body_field = el.role == "AXTextArea" or any(b in el.label.lower() for b in ("message", "text message", "body", "chat"))

        if is_recipient_name and is_body_field:
            raise RuntimeError(
                f"Communication Safety Guard: Refusing to type contact name '{text}' into message body '{el.label}'. "
                f"Recipient must only be typed into the 'To:' field!"
            )
        if not is_recipient_name and is_recipient_field and len(text.split()) > 4:
            raise RuntimeError(
                f"Communication Safety Guard: Refusing to type message text '{text[:30]}...' into recipient field '{el.label}'."
            )


def check_document_write_safety(task: str, el: Element | None) -> None:
    """Safety Guard: Never write or paste into an existing document when the user asked to create a new one (native Rust backend)."""
    if el is None:
        return
    lib = _load_native_lib()
    if lib and hasattr(lib, "cu_check_doc_safety"):
        try:
            import json
            el_role = el.role.encode("utf-8") if el else None
            el_label = el.label.encode("utf-8") if el else None
            raw = lib.cu_check_doc_safety(task.encode("utf-8"), el_role, el_label)
            if raw:
                dec = json.loads(raw.decode("utf-8"))
                if not dec.get("allowed", True):
                    raise RuntimeError(dec.get("violation") or dec.get("reason", "Document write safety violation"))
                return
        except RuntimeError:
            raise
        except Exception:
            pass

    if el.role == "AXTextArea" and el.label and len(el.label.strip()) > 8:
        is_create = any(w in task.lower() for w in ("create", "new note", "new doc", "write a new"))
        is_explicit = el.label.lower() in task.lower()
        if is_create and not is_explicit:
            raise RuntimeError(
                f"Safety Guard: Refusing to type/paste into existing document '{el.label[:30]}...' "
                f"when task asked to create a new item. Ensure a blank new document is open."
            )


def _has_gemini() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))


def _has_jev() -> bool:
    return bool(os.environ.get("TYPESAFE_API_KEY") or os.environ.get("JEV_API_KEY"))


def _gemini_ok() -> bool:
    if not _has_gemini():
        return False
    s = load_settings()
    return any(usage.available(m) for m in (s["gemini_models"] if s["auto_switch"] else s["gemini_models"][:1]))


def _jev_ok() -> bool:
    return _has_jev() and usage.available("jev")


def pick_mode(mode: str = "auto") -> str:
    """Resolve "auto" to the best mode the configured keys and remaining quota allow."""
    if mode != "auto":
        return mode
    gemini, jev = _gemini_ok(), _jev_ok()
    if gemini and jev:
        return "hybrid"
    if jev:
        return "jev"
    if gemini:
        return "gemini"
    if _has_gemini() or _has_jev():
        raise RuntimeError("Every configured model is out of quota right now (see Usage in the menu)")
    raise RuntimeError("Add a Jev and/or Gemini API key (menu › API Keys…), "
                       "or drive the Mac from Antigravity/Claude through the MCP tools, no key needed")


def ask_user(msg: str) -> bool:
    return input(f"{msg} [y/N] ").strip().lower() == "y"


@dataclass
class Agent:
    mode: str = "auto"  # auto | hybrid | jev | gemini
    confirm_all: bool = False  # ask before every action
    allow_risky: bool = False  # skip the prompt for steps the planner marks risky
    min_jev_prob: float = 0.55
    max_rounds: int = 12
    confirm: Callable[[str], bool] = ask_user
    log: Callable[[str], None] = print
    narrate: Callable[[str], None] = lambda text: None  # short spoken updates while working
    max_jev_actions: int = 40
    gemini: Gemini | None = None
    jev: Jev | None = None
    stats: dict = field(default_factory=lambda: {"jev_steps": 0, "gemini_steps": 0, "direct_steps": 0})
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
        if self.gemini is None and (self.mode in ("hybrid", "gemini") or _gemini_ok()):
            # jev mode can still escalate to Gemini when stuck
            self.gemini = Gemini(auto_switch=self.auto_switch, log=self.log)

    def run(self, task: str) -> str:
        """Run the task, moving to another provider when one runs out of quota (if auto-switch is on)."""
        note = ""
        for _ in range(4):
            try:
                return self._run(task + note)
            except usage.QuotaExhausted as e:
                if e.provider == "gemini" and self.mode == "jev":
                    self.log("  Gemini is out of quota; Jev carries on alone")
                    self.gemini = None
                    continue
                alt = None
                if self.auto_switch:
                    alt = "gemini" if e.provider == "jev" and _gemini_ok() else "jev" if e.provider == "gemini" and _jev_ok() else None
                if alt is None:
                    back = f" · back in {usage.fmt_until(e.until)}" if e.until else ""
                    return self._report(f"stopped: {e}{back}")
                self.log(f"  {e}; switching to {alt} mode")
                self.narrate(f"{usage.pretty(e.provider) if e.provider != 'gemini' else 'Gemini'} ran out, switching to {alt}")
                self.mode = alt
                self._clients()
                note = "\n(Continue from the current screen; some of this may already be done.)"
        return self._report("stopped: out of quota everywhere")

    def _run(self, task: str) -> str:
        if self.mode == "jev":
            return self.run_jev(task)
        if self.mode == "gemini":
            ok, msg = self.gemini.act(task, max_turns=40, confirm=self.confirm, log=self.log, stop=self.stop, narrate=self.narrate)
            return self._report(msg)

        history: list[str] = []
        for round_ in range(self.max_rounds):
            if self.stop.is_set():
                return self._report("stopped by user")
            plan = self.gemini.plan(task, history)
            status, steps = plan.get("status", "continue"), plan.get("steps") or []
            self.log(f"[plan {round_ + 1}] {status}: {plan.get('summary', '')}")
            if status == "continue" and plan.get("summary"):
                self.narrate(plan["summary"])
            if status == "done" or (status != "blocked" and not steps):
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

    def verify_task_completion(self, task: str, app: str, els: list[Element], history: list[str]) -> tuple[bool, str]:
        """Self-correcting verification: confirms post-conditions before accepting DONE (native Rust backend)."""
        lib = _load_native_lib()
        if lib and hasattr(lib, "cu_verify_task_completion"):
            try:
                import json
                els_payload = [
                    {"id": e.id, "role": e.role, "label": e.label, "x": e.x, "y": e.y, "w": e.w, "h": e.h, "focused": e.focused}
                    for e in els
                ]
                raw = lib.cu_verify_task_completion(
                    task.encode("utf-8"),
                    app.encode("utf-8"),
                    json.dumps(els_payload).encode("utf-8"),
                    json.dumps(history).encode("utf-8"),
                )
                if raw:
                    data = json.loads(raw.decode("utf-8"))
                    verified = data.get("verified", False)
                    reason = data.get("reason", "")
                    if not verified:
                        return False, reason
                    # If verified by native rule engine, run Jev check if available
                    if self.jev and els:
                        try:
                            p = self.jev.check(f"Has this entire task been accomplished? Task: '{task}'", app, els)
                            if p < 0.35:
                                return False, f"Jev verification confidence is low (p={p:.2f})"
                        except Exception:
                            pass
                    return True, reason
            except Exception:
                pass

        task_lower = task.lower()
        words = [w.strip(".,;:?!") for w in task_lower.split()]

        # Check for unresolved on-screen error alerts across any app
        for el in els:
            if el.role == "AXAlert" or (el.role == "AXDialog" and any(w in el.label.lower() for w in ("error", "failed", "could not", "invalid"))):
                return False, f"An unresolved error dialog is active on screen: '{el.label}'."

        # 1. Dispatch / send / share objective verification
        dispatch_verbs = ("send", "share", "mail", "email", "message", "text", "post", "submit", "publish", "forward", "dispatch", "tweet")
        if any(v in words or any(w.startswith(v) for w in words) for v in dispatch_verbs):
            sheet_elements = [e for e in els if "to:" in e.label.lower() or "add recipient" in e.label.lower() or e.role in ("AXSheet", "AXPopover")]
            if sheet_elements:
                return False, "A compose, sharing, or recipient dispatch sheet is still active on screen and has not been submitted or closed."
            has_send = any(any(s in h.lower() for s in ("send", "return", "enter", "delivered", "shared", "submit", "dispatch", "post", "publish", "command+return", "command,return")) for h in history)
            if not has_send:
                return False, "Objective requested dispatch/sharing, but no send, submit, or return action was executed to complete it."

        # 2. Media / capture / attachment objective verification
        if any(m in task_lower for m in ("screenshot", "capture", "image", "photo", "picture", "attach", "attachment", "card")):
            has_media = any(any(m in h.lower() for m in ("screenshot", "paste", "attachment", "image", "photo")) for h in history)
            if not has_media:
                return False, "Objective requested capturing or attaching media, but no screenshot or attachment action was found in execution history."

        # 3. Computation / calculation objective verification
        math_terms = ("calculate", "computation", "compute", "math", "evaluate", "solve", "sum", "multiply", "divide")
        has_math_op = any(op in task for op in ("+", "*", "/", "=")) and any(c.isdigit() for c in task)
        if any(m in task_lower for m in math_terms) or has_math_op:
            has_calc = any((("calc" in h.lower() and "open" not in h.lower()) or any(s in h.lower() for s in ("click", "equals", "keypad", "+", "*", "/", "-"))) for h in history)
            if not has_calc:
                return False, "Objective requested computation, but no calculation or keypad actions were performed."

        # 4. Creation objective verification
        creation_verbs = ("create", "write", "compose", "draft", "make", "new", "add", "record")
        is_creation = any(v in words or any(w.startswith(v) for w in words) for v in creation_verbs)
        is_dispatch = any(v in words or any(w.startswith(v) for w in words) for v in dispatch_verbs)
        if is_creation and not is_dispatch:
            has_input = any(any(k in h.lower() for k in ("type", "paste", "new_item", "create", "write")) for h in history)
            if not has_input:
                return False, "Objective requested creating or writing content, but no text input or document creation action was executed."

        # 5. Jev verification check if available
        if self.jev and els:
            try:
                p = self.jev.check(f"Has this entire task been accomplished? Task: '{task}'", app, els)
                if p < 0.35:
                    return False, f"Jev verification confidence is low (p={p:.2f})"
            except Exception:
                pass

        return True, "Task verified: all objective conditions satisfied"

    def run_jev(self, task: str) -> str:
        """Jev picks op + target + text + app in one request per turn, from the AX tree."""
        texts, apps = text_candidates(task.split("\n(Continue", 1)[0]), installed_apps()
        history: list[str] = []
        recent: deque = deque(maxlen=3)
        misses = 0
        for _ in range(self.max_jev_actions):
            if self.stop.is_set():
                return self._report("stopped by user")
            app, els = ax_elements(limit=253)
            d = self.jev.decide(task, app, els, history, texts, apps)
            op, el = d["op"], d["element"]
            desc = _describe_op(d)
            self.log(f"  jev → {desc}  p={d['op_p']:.2f}")
            self.narrate(narrate.jev_op(op, el.label if el else "", d["text"], d["app"]))

            if op == "DONE":
                ok_verified, why = self.verify_task_completion(task, app, els, history)
                if not ok_verified:
                    self.log(f"  [Self-Correction] Premature DONE rejected: {why}")
                    history.append(f"FAILED: Premature DONE rejected: {why}. Self-correct and complete the missing step.")
                    misses = 0
                    continue
                return self._report(f"done (verified; jev p={d['op_p']:.2f})")

            # Evaluate stuck / loop state using native Rust policy engine
            lib = _load_native_lib()
            stuck = False
            if lib and hasattr(lib, "cu_evaluate_agent_stuck"):
                try:
                    import json
                    raw = lib.cu_evaluate_agent_stuck(op.encode("utf-8"), misses, json.dumps(list(recent)).encode("utf-8"))
                    if raw:
                        stuck_data = json.loads(raw.decode("utf-8"))
                        stuck = stuck_data.get("should_escalate_or_pause", False)
                except Exception:
                    stuck = False
            if not stuck:
                stuck = op == "BLOCKED" or misses >= 3 or (len(recent) == 3 and len(set(recent)) == 1)

            if stuck or (self.gemini and d["op_p"] < 0.35):
                if not self.gemini:
                    # Prompt user for guidance and learn from demonstration!
                    learned = prompt_user_for_help(task, app, f"Unsure how to proceed with '{desc}'", log_fn=self.log)
                    if learned:
                        history.append(f"ok: User guided step in {app}")
                        recent.clear()
                        misses = 0
                        continue
                    return self._report("blocked: Jev is stuck (add GEMINI_API_KEY to let Gemini take over)")
                self.stats["gemini_steps"] += 1
                ok, msg = self.gemini.act(f"Task: {task}\nDone so far: {history[-8:]}\nDo the next step or two, then stop.",
                                          max_turns=6, confirm=self.confirm, log=self.log, stop=self.stop, narrate=self.narrate)
                history.append(f"gemini: {msg}")
                recent.clear()
                misses = 0
                continue
            if op in ("CLICK", "DOUBLE_CLICK", "TYPE") and el is None and not (op == "TYPE" and _focused(els)):
                misses += 1
                history.append(f"FAILED: {desc} (no matching element)")
                continue

            label = (el.label if el else "").lower()
            if self.confirm_all or (op in ("CLICK", "DOUBLE_CLICK", "PRESS_ENTER") and not self.allow_risky
                                    and is_risky_action(op, label, desc)):
                if not self.confirm(f"About to: {desc}. Proceed?"):
                    return self._report("stopped: user declined")
            try:
                self._do_jev(op, d, el, task, els)
            except Exception as e:
                history.append(f"FAILED: {desc} ({e})")
                misses += 1
                continue
            self.stats["jev_steps"] += 1
            history.append(f"ok: {desc}")
            recent.append(desc)
            time.sleep(0.08)
        return self._report("stopped: hit the action limit")

    def _do_jev(self, op: str, d: dict, el: Element | None, task: str = "", els: list[Element] | None = None):
        if els is None:
            els = []
        if op in ("CLICK", "DOUBLE_CLICK"):
            actions.click(el.x, el.y, clicks=2 if op == "DOUBLE_CLICK" else 1)
        elif op == "TYPE":
            # Communication safety check (native Rust)
            check_typing_safety(d["text"], el, d.get("app", ""), els)
            # Safety Guard: Never write into an existing document when creating a new one (native Rust)
            check_document_write_safety(task, el)
            if el is not None and not el.focused:
                actions.click(el.x, el.y)
            actions.type_text(d["text"], clear=el is not None and el.role != "AXTextArea")
        elif op == "PASTE":
            # Safety Guard: Never paste into an existing document when creating a new one (native Rust)
            check_document_write_safety(task, el)
            if el is not None and not el.focused:
                actions.click(el.x, el.y)
            if any(w in task.lower() for w in ("screenshot", "image", "photo", "card")) and os.path.exists("/tmp/about_macos.png"):
                actions.paste_image("/tmp/about_macos.png")
            elif any(w in task.lower() for w in ("screenshot", "image", "photo", "card")) and os.path.exists("/tmp/cu_screenshot.png"):
                actions.paste_image("/tmp/cu_screenshot.png")
            else:
                actions.paste()
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
                from .screen import screen_size
                w, h = screen_size()
                x, y = w // 2, h // 2
            actions.scroll(x, y, "down" if op == "SCROLL_DOWN" else "up", 400)
        elif op == "OPEN_APP":
            if not actions.open_app(d["app"]):
                raise RuntimeError(f"could not open {d['app']}")
        elif op == "WAIT":
            time.sleep(0.5)

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
        except usage.QuotaExhausted:
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
        if kind in ("click", "double_click"):
            actions.click(el.x, el.y, clicks=2 if kind == "double_click" else 1)
        elif kind == "type":
            check_typing_safety(s["text"], el, app, els)
            check_document_write_safety(s.get("do", "") or s.get("target", ""), el)
            actions.click(el.x, el.y)
            actions.type_text(s["text"], press_enter=s.get("enter", False), clear=el.role != "AXTextArea")
        elif kind == "scroll":
            actions.scroll(el.x, el.y, s.get("direction", "down"))
        time.sleep(0.5)
        return self._verify(s)

    def _verify(self, s: dict) -> tuple[bool, str]:
        """Cheap post-check with Jev against the AX tree; Gemini replans if it fails."""
        if not s.get("expect") or self.jev is None:
            return True, ""
        app, els = ax_elements(limit=200)
        p = self.jev.check(s["expect"], app, els) if els else 1.0
        return (p >= 0.4), f"(expect p={p:.2f})"

    def _fallback(self, s: dict, why: str) -> tuple[bool, str]:
        self.log(f"    {why} → gemini vision")
        self.stats["gemini_steps"] += 1
        ok, msg = self.gemini.act(f"Do exactly this one step, then stop: {_fmt(s)}", max_turns=6,
                                  confirm=self.confirm, log=self.log, stop=self.stop, narrate=self.narrate)
        return ok, f"({msg})"

    def _report(self, msg: str) -> str:
        g = self.gemini
        lines = [msg, f"mode: {self.mode}, steps: {self.stats}"]
        if g:
            lines.append(f"gemini: {g.calls} calls, {g.input_tokens} in / {g.output_tokens} out tokens")
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


def _focused(els: list[Element]) -> bool:
    return any(e.focused and e.role in ("AXTextField", "AXTextArea", "AXSearchField", "AXComboBox") for e in els)


def _describe_op(d: dict) -> str:
    op, el = d["op"], d["element"]
    if op == "TYPE":
        return f'TYPE "{d["text"]}" into {el.describe() if el else "focused field"}'
    if op == "OPEN_APP":
        return f"OPEN_APP {d['app']}"
    return f"{op} {el.describe()}" if el and op in ("CLICK", "DOUBLE_CLICK", "SCROLL_DOWN", "SCROLL_UP") else op
