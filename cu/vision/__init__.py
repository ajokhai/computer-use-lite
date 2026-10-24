"""The vision model: plans for the Vision + Jev mode and does vision computer use.

Providers: Claude, OpenAI and Gemini (each through its own computer-use tool), OpenRouter (any
model it offers) and a local OpenAI-compatible server (Ollama, LM Studio…). `Vision` picks the
model from the "vision_model" setting ("auto" = the first provider in "vision_order" with a key
and quota left) and, when switching is on, moves to the next model or provider as quota runs out.
Every provider has the same interface: plan(task, history) and act(task, …) → (ok, message)."""

import os
from typing import Callable

from .. import usage
from ..config import load_settings
from .base import CONTINUE_NOTE, VisionModel, is_risky_action, parse_json  # noqa: F401

PROVIDERS = ("claude", "openai", "gemini", "openrouter", "local")
LABELS = {"claude": "Claude", "openai": "OpenAI", "gemini": "Gemini", "openrouter": "OpenRouter", "local": "Local model"}
KEY_NAMES = {"claude": ("ANTHROPIC_API_KEY",), "openai": ("OPENAI_API_KEY",),
             "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"), "openrouter": ("OPENROUTER_API_KEY",), "local": ()}


def provider_of(model: str) -> str:
    if ":" in model:
        return model.split(":", 1)[0]
    for p in ("claude", "gemini"):
        if model.startswith(p):
            return p
    return "openai"


def models(provider: str, s: dict | None = None) -> list[str]:
    """The provider's models in the order they're tried (usage ids: "local:<name>" for compatible ones)."""
    s = s or load_settings()
    if provider in ("openrouter", "local"):
        name = s.get(f"{provider}_model") or ""
        return [f"{provider}:{name}"] if name else []
    return list(s.get(f"{provider}_models") or [])


def has_key(provider: str) -> bool:
    """Whether the provider can be used at all: a key, or for a local server a model name."""
    if provider == "local":
        return bool(load_settings().get("local_model"))
    names = KEY_NAMES.get(provider, ())
    return any(os.environ.get(n) for n in names) and bool(models(provider))


def order(s: dict | None = None) -> list[tuple[str, list[str]]]:
    """(provider, models) in the order to try them. A chosen model goes first; with switching on,
    every other provider follows as a fallback."""
    s = s or load_settings()
    seq = [p for p in s.get("vision_order") or PROVIDERS if p in PROVIDERS]
    seq += [p for p in PROVIDERS if p not in seq]
    choice = s.get("vision_model") or "auto"
    out = [(p, models(p, s)) for p in seq]
    if choice != "auto":
        first = provider_of(choice)
        chosen = [choice, *[m for m in models(first, s) if m != choice]]
        out = [(first, chosen), *[(p, ms) for p, ms in out if p != first]]
        if not s.get("auto_switch", True):
            out = [(first, chosen[:1])]
    return [(p, ms) for p, ms in out if ms]


def configured() -> bool:
    """Any vision model set up (a key, or a local model)."""
    return any(has_key(p) for p, _ in order())


def available() -> bool:
    """Any vision model set up with quota left."""
    s = load_settings()
    for p, ms in order(s):
        if has_key(p) and any(usage.available(m) for m in (ms if s["auto_switch"] else ms[:1])):
            return True
    return False


def make(provider: str, ms: list[str], auto_switch: bool, log: Callable[[str], None]) -> VisionModel:
    """One SDK client for the provider (imported on first use, so unused SDKs cost no memory)."""
    if provider == "claude":
        from .claude import Claude as cls
    elif provider == "openai":
        from .openai import OpenAI as cls
    elif provider == "gemini":
        from .gemini import Gemini as cls
    elif provider == "openrouter":
        from .compat import OpenRouter as cls
    elif provider == "local":
        from .compat import Local
        return Local(ms, auto_switch, log, url=load_settings().get("local_url"))
    else:
        raise ValueError(f"unknown vision provider {provider}")
    return cls(ms, auto_switch, log)


class Vision:
    """The vision model the agent uses. Holds at most one client per provider."""

    def __init__(self, auto_switch: bool | None = None, log: Callable[[str], None] = print, clients: dict | None = None):
        s = load_settings()
        self.auto_switch = s["auto_switch"] if auto_switch is None else auto_switch
        self.log = log
        self.clients: dict[str, VisionModel] = dict(clients or {})
        self.order = order(s)
        if clients:  # tests and callers that bring their own clients use exactly those
            self.order = [(p, c.models) for p, c in self.clients.items()]
        self.exhausted: set[str] = set()

    def _candidates(self):
        seq = self.order if self.auto_switch else self.order[:1]
        return [(p, ms) for p, ms in seq if p not in self.exhausted and (p in self.clients or has_key(p))]

    def current(self) -> VisionModel:
        """The first provider with a key and quota left, its client created on first use."""
        for p, ms in self._candidates():
            client = self.clients.get(p)
            if client is None:
                try:
                    client = self.clients[p] = make(p, ms, self.auto_switch, self.log)
                except Exception as e:
                    self.log(f"  {LABELS.get(p, p)} can't be used: {e}")
                    self.exhausted.add(p)
                    continue
            if client.available():
                return client
        raise usage.QuotaExhausted("vision", None, "every vision model")

    @property
    def label(self) -> str:
        try:
            m = self.current()
            return usage.pretty(m.model)
        except usage.QuotaExhausted:
            return "the vision model"

    def _switch(self, e: usage.QuotaExhausted) -> bool:
        """After a provider ran out: move on if allowed and something else is left."""
        if not self.auto_switch or e.provider == "vision":
            return False
        self.exhausted.add(e.provider)
        try:
            nxt = self.current()
        except usage.QuotaExhausted:
            return False
        self.log(f"  {e}; switching to {usage.pretty(nxt.model)}")
        return True

    def plan(self, task: str, history: list[str]) -> dict:
        while True:
            try:
                return self.current().plan(task, history)
            except usage.QuotaExhausted as e:
                if not self._switch(e):
                    raise usage.QuotaExhausted("vision", e.until, str(e)) from e

    def act(self, instruction: str, max_turns: int = 15, **kw) -> tuple[bool, str]:
        while True:
            try:
                return self.current().act(instruction, max_turns, **kw)
            except usage.QuotaExhausted as e:
                if not self._switch(e):
                    raise usage.QuotaExhausted("vision", e.until, str(e)) from e
                instruction += CONTINUE_NOTE

    def ask_json(self, system: str, text: str) -> dict:
        """One question about the current screen, answered as JSON (used to assess a failed attempt)."""
        from ..screen import screenshot_png
        while True:
            try:
                return parse_json(self.current()._json(system, text, screenshot_png()))
            except usage.QuotaExhausted as e:
                if not self._switch(e):
                    raise usage.QuotaExhausted("vision", e.until, str(e)) from e

    @property
    def calls(self) -> int:
        return sum(c.calls for c in self.clients.values())

    @property
    def input_tokens(self) -> int:
        return sum(c.input_tokens for c in self.clients.values())

    @property
    def output_tokens(self) -> int:
        return sum(c.output_tokens for c in self.clients.values())

    def report(self) -> list[str]:
        return [f"{c.label}: {c.calls} calls, {c.input_tokens} in / {c.output_tokens} out tokens"
                for c in self.clients.values() if c.calls]
