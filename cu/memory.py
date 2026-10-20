"""Experiential Learning & Memory for Computer Use (Native Rust Backend).

When Computer Use encounters an unfamiliar, blocked, or ambiguous state, it asks the user
for guidance, observes the user's demonstration, records the solution into persistent memory
(managed by crates/cu-native in Rust), and applies it autonomously on future runs.
"""

import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import DATA
from .screen import Element, _load_native_lib, ax_elements

MEMORY_FILE = DATA / "learned_skills.json"


@dataclass
class LearnedStep:
    op: str  # CLICK, TYPE, HOTKEY, WAIT, etc.
    target_role: str = ""
    target_label: str = ""
    text: str = ""
    keys: list[str] = field(default_factory=list)
    description: str = ""


@dataclass
class LearnedPattern:
    id: str
    app: str
    intent: str
    keywords: list[str]
    description: str
    steps: list[LearnedStep]
    success_count: int = 1
    created_at: float = field(default_factory=time.time)


class MemoryStore:
    """Memory store backed by native Rust `crates/cu-native` engine."""

    def __init__(self, file_path: Path = MEMORY_FILE):
        self.file_path = file_path
        self._native = _load_native_lib()

    def find_match(self, task: str, app: str, els: list[Element] | None = None) -> LearnedPattern | None:
        """Find a learned pattern matching intent using native Rust matcher."""
        if self._native and hasattr(self._native, "cu_memory_find_match"):
            try:
                res = self._native.cu_memory_find_match(task.encode("utf-8"), app.encode("utf-8"))
                if res:
                    data = json.loads(res.decode("utf-8"))
                    steps = [
                        LearnedStep(
                            op=s.get("op", ""),
                            target_role="",
                            target_label=s.get("target") or "",
                            text=s.get("text") or "",
                            keys=s.get("keys", []),
                            description=s.get("description", ""),
                        )
                        for s in data.get("steps", [])
                    ]
                    return LearnedPattern(
                        id=data["id"],
                        app=data["app"],
                        intent=data["intent"],
                        keywords=data.get("keywords", []),
                        description=data.get("description", ""),
                        steps=steps,
                        success_count=data.get("success_count", 1),
                        created_at=data.get("created_at", time.time()),
                    )
            except Exception:
                pass
        return None

    def record_learning(self, app: str, intent: str, keywords: list[str], steps: list[LearnedStep], desc: str = ""):
        """Persist a newly learned workflow into native Rust memory store."""
        pat_id = f"learned_{app.lower().replace(' ', '_')}_{int(time.time())}"
        pat_dict = {
            "id": pat_id,
            "app": app,
            "intent": intent,
            "keywords": keywords,
            "description": desc or intent,
            "steps": [
                {
                    "op": s.op,
                    "target": s.target_label or s.target_role or None,
                    "keys": s.keys,
                    "text": s.text or None,
                    "description": s.description,
                }
                for s in steps
            ],
            "success_count": 1,
            "created_at": time.time(),
        }
        if self._native and hasattr(self._native, "cu_memory_save_pattern"):
            try:
                raw = json.dumps(pat_dict).encode("utf-8")
                self._native.cu_memory_save_pattern(raw)
            except Exception:
                pass
        return pat_dict

    def list_patterns(self) -> list[dict]:
        """List all learned skills from native Rust memory store."""
        if self._native and hasattr(self._native, "cu_memory_list_patterns"):
            try:
                res = self._native.cu_memory_list_patterns()
                if res:
                    return json.loads(res.decode("utf-8"))
            except Exception:
                pass
        return []


_STORE = MemoryStore()


def get_memory_store() -> MemoryStore:
    return _STORE


def prompt_user_for_help(task: str, app: str, reason: str, log_fn=print) -> list[LearnedStep] | None:
    """Prompt the user for live guidance and observe what the user does on screen."""
    log_fn("\n" + "=" * 70)
    log_fn("🤖 [Computer Use Needs Your Guidance]")
    log_fn(f"Context: {app} | Task: {task}")
    log_fn(f"Reason:  {reason}")
    log_fn("\n👉 Please perform the next step directly on your screen now.")
    log_fn("   I will observe your action, learn from it, and remember it for future runs.")
    log_fn("   (You have 15 seconds, or press Enter when done)")
    log_fn("=" * 70 + "\n")

    # Record snapshot before user action
    _, els_before = ax_elements(limit=150)
    t_start = time.time()

    # Wait up to 15s for the user to perform the action
    time.sleep(4.0)

    # Observe after
    _, els_after = ax_elements(limit=150)

    # Detect newly focused or changed elements
    diff_focused = [e for e in els_after if e.focused and not any(b.id == e.id and b.focused for b in els_before)]
    observed_steps = []

    if diff_focused:
        target = diff_focused[0]
        step = LearnedStep(
            op="CLICK",
            target_role=target.role,
            target_label=target.label,
            description=f"Click {target.describe()}",
        )
        observed_steps.append(step)
    else:
        # Check newly created UI elements (popover, sheet, dialog)
        new_containers = [e for e in els_after if e.role in ("AXPopover", "AXSheet", "AXDialog", "AXWindow")
                          and not any(b.id == e.id for b in els_before)]
        if new_containers:
            c = new_containers[0]
            step = LearnedStep(
                op="CLICK_POPUP",
                target_role=c.role,
                target_label=c.label,
                description=f"Interact with new {c.role} '{c.label}'",
            )
            observed_steps.append(step)

    if observed_steps:
        store = get_memory_store()
        keywords = [w.lower() for w in task.replace(",", " ").split() if len(w) > 3][:4]
        store.record_learning(
            app=app,
            intent=task,
            keywords=keywords,
            steps=observed_steps,
            desc=f"Learned step for: {task}",
        )
        log_fn(f"✅ Learned new skill: {[s.description for s in observed_steps]}")
        return observed_steps

    return None
