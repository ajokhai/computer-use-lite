"""Error recovery across attempts: a failed attempt isn't the end. The agent looks at what
happened, works out why and what to try instead, tries again with that guidance, and saves what
it learned (cu/memory.py) so the next run starts from it.

Not retried: the user's own stop, a declined confirmation, the corner abort, and a model asking for
the user (credentials, 2FA, a decision): those need a person, not another try."""

import json
import re
from dataclasses import dataclass, field
from typing import Callable

from . import memory

MAX_ATTEMPTS = 3
NOTES = "\n\n[Notes for this attempt]"  # everything after this in a task is guidance, not the task

NO_RETRY = ("stopped by user", "stopped: user declined", "user declined", "stopped: mouse moved into a screen corner",
            "needs user", "blocked: needs", "stopped: out of quota", "out of quota everywhere")

ASSESS_SYSTEM = """You review a failed attempt by a computer-use agent on macOS and decide how to retry.
You get the task, the log of what it did (with errors), lessons from earlier runs, and a screenshot of
the screen now. Reply with JSON only:
{"diagnosis": "<one sentence: what went wrong and why>",
 "next_try": "<concrete instructions for the next attempt: what to do differently, step by step>",
 "lesson": "<one reusable sentence for next time, about this app or site, without personal details>",
 "give_up": false}
Set give_up true only if retrying can't help (it needs the user, an account, a payment, or the task
is impossible). Prefer a different route over repeating: keyboard shortcuts, a direct URL, another
element, scrolling, waiting for a page to load, closing a dialog that's in the way."""

# For Jev alone (it can't write text): known failure patterns → what to do differently.
RULES = [
    (r"no matching element|stuck at 'CLICK'|went blind|no elements",
     "The element wasn't in the accessibility list. Wait for the page to load, scroll, or use the "
     "keyboard (Tab, a shortcut) or a direct URL instead of clicking."),
    (r"isn't a text field|no text field has focus",
     "Click the text box itself (its label is usually like 'Post text', 'Message', 'Search') before typing."),
    (r"premature|not done yet",
     "It wasn't finished: check the result on screen and do the remaining step (often pressing a Post/Send/Save button)."),
    (r"ran out of turns|hit the (round|action) limit",
     "It took too many steps. Go more directly: open the exact URL or use the app's shortcut for the action."),
    (r"no app named|could not open",
     "That app name didn't open. Use the exact name from /Applications, or open the website instead."),
]


def retryable(report: str) -> bool:
    first = (report or "").strip().splitlines()[0].lower() if report else ""
    return not any(first.startswith(p) or p in first for p in NO_RETRY)


def strip_notes(task: str) -> str:
    """The task without guidance added by recovery (and without the old continue note)."""
    return task.split(NOTES, 1)[0].split("\n(Continue", 1)[0]


def notes_of(task: str, limit: int = 8) -> list[str]:
    """The guidance lines recovery added to a task (for models like Jev that take hints, not prose)."""
    if NOTES not in task:
        return []
    return [ln.strip() for ln in task.split(NOTES, 1)[1].splitlines() if ln.strip()][:limit]


def with_notes(task: str, notes: list[str]) -> str:
    notes = [n for n in notes if n]
    return strip_notes(task) + (NOTES + "\n" + "\n".join(notes) if notes else "")


def rule_based(log: list[str]) -> dict:
    text = "\n".join(log[-40:])
    for pattern, advice in RULES:
        if re.search(pattern, text, re.I):
            return {"diagnosis": f"matched: {pattern.split('|')[0]}", "next_try": advice, "lesson": advice, "give_up": False}
    return {"diagnosis": "unclear", "next_try": "Try a different route than last time: keyboard shortcuts, a direct URL, "
            "or another element with a similar label.", "lesson": "", "give_up": False}


@dataclass
class Recovery:
    """Runs a task in up to MAX_ATTEMPTS attempts.

    attempt(task) -> (ok, report): one full attempt by the agent.
    log_tail() -> the agent's recent log lines (for the assessment).
    ask_json(system, text) -> dict | None: one look at the screen by a vision model, or None if there's none.
    where() -> the frontmost app (for keying lessons)."""

    attempt: Callable[[str], tuple[bool, str]]
    log_tail: Callable[[], list[str]]
    ask_json: Callable[[str, str], dict | None] = lambda system, text: None
    where: Callable[[], str] = lambda: ""
    log: Callable[[str], None] = print
    model: str = ""
    max_attempts: int = MAX_ATTEMPTS
    history: list[dict] = field(default_factory=list)

    def run(self, task: str) -> tuple[bool, str]:
        task = strip_notes(task)
        keys = memory.places(task, self.where())
        past = memory.recall(keys, task)
        notes = [f"Lessons from earlier runs:\n{memory.as_text(past)}"] if past else []
        ok, report = False, ""
        for n in range(1, self.max_attempts + 1):
            ok, report = self.attempt(with_notes(task, notes))
            if ok:
                if self.history:  # it took a retry: the guidance that got it there is worth keeping
                    last = self.history[-1]
                    self._learn(keys, task, last["diagnosis"], last.get("lesson") or last["next_try"], "worked")
                return ok, report
            if not retryable(report) or n == self.max_attempts:
                break
            verdict = self._assess(task, report, past)
            self.history.append(verdict)
            self.log(f"  attempt {n} failed: {verdict['diagnosis']}")
            if verdict.get("give_up"):
                self.log("  not retrying: it needs you")
                break
            self.log(f"  trying again: {verdict['next_try']}")
            notes = [n_ for n_ in notes if n_.startswith("Lessons")]
            notes.append(f"Attempt {n} failed: {verdict['diagnosis']}\nThis time: {verdict['next_try']}")
            keys = memory.places(task, self.where()) or keys
        if self.history:  # what didn't work is a lesson too
            last = self.history[-1]
            self._learn(keys, task, last["diagnosis"], last.get("lesson") or last["next_try"], "failed")
        return ok, report

    def _assess(self, task: str, report: str, past: list[dict]) -> dict:
        log = self.log_tail()
        text = json.dumps({"task": task, "result": report.splitlines()[0] if report else "",
                           "log": log[-40:], "lessons": memory.as_text(past),
                           "earlier_attempts": self.history[-2:]})
        try:
            verdict = self.ask_json(ASSESS_SYSTEM, text)
        except Exception as e:  # the assessor itself failing must not end the task
            self.log(f"  couldn't assess with the vision model ({e}); using known patterns")
            verdict = None
        if not isinstance(verdict, dict) or not verdict.get("next_try"):
            verdict = rule_based(log + [report])
        verdict.setdefault("diagnosis", "unclear")
        verdict.setdefault("lesson", "")
        verdict["give_up"] = bool(verdict.get("give_up"))
        return verdict

    def _learn(self, keys: list[str], task: str, what: str, fix: str, outcome: str):
        if not fix:
            return
        for where in keys[:2] or ["general"]:
            memory.record(where, task, what, fix, outcome, self.model)
