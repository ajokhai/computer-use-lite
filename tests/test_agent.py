"""The Jev agent loop with a scripted fake Jev and no real input: the model decides when a
task is done, being stuck ends the task, and the user's stops are honoured."""

import pytest

from cu import actions, agent
from cu.screen import Element

BUTTON = Element(id="e0", role="AXButton", label="Send", x=10, y=10, w=40, h=20)
FIELD = Element(id="e1", role="AXTextField", label="Note", x=10, y=60, w=200, h=20)


class FakeJev:
    """Plays back (op, element, text) decisions; check() returns `done_p`."""

    input_tokens, cost_usd = 0, 0.0

    def __init__(self, script, done_p=0.9):
        self.script, self.done_p, self.checks = list(script), done_p, 0

    def decide(self, task, app, els, history, texts, apps, tried=None):
        op, el, text = self.script.pop(0) if self.script else ("BLOCKED", None, "")
        return {"op": op, "op_p": 0.9, "element": el, "target_p": 0.9, "text": text, "app": ""}

    def check(self, task, app, els, history=None):
        self.checks += 1
        return self.done_p

    def locate(self, desc):
        return None, 0.0


@pytest.fixture
def desk(monkeypatch):
    """Record actions instead of performing them."""
    done = []
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr(agent, "ax_elements", lambda limit=200: ("Notes", [BUTTON, FIELD]))
    monkeypatch.setattr(agent, "installed_apps", lambda: ["Notes"])
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)
    monkeypatch.setattr(actions, "click", lambda x, y, clicks=1, **k: done.append(f"click {x},{y}"))
    monkeypatch.setattr(actions, "type_text", lambda text, **k: done.append(f"type {text}"))
    monkeypatch.setattr(actions, "hotkey", lambda keys: done.append("+".join(keys)))
    return done


def run(script, done_p=0.9, confirm=lambda m: True, allow_risky=False):
    a = agent.Agent(mode="jev", jev=FakeJev(script, done_p), confirm=confirm, allow_risky=allow_risky,
                    auto_switch=False, log=lambda s: None)
    return a, a.run("type 'hello' into the note")


def test_done_is_accepted_when_the_model_confirms(desk):
    a, out = run([("TYPE", FIELD, "hello"), ("DONE", None, "")])
    assert out.startswith("done")
    assert desk == ["click 10,60", "type hello"]  # focuses the field, then types
    assert a.jev.checks == 1


def test_premature_done_keeps_working(desk):
    # The model doubts DONE, so the loop carries on rather than stopping on a guess.
    a, out = run([("DONE", None, ""), ("TYPE", FIELD, "hello"), ("DONE", None, "")], done_p=0.1)
    assert desk == ["click 10,60", "type hello"]  # focuses the field, then types
    assert not out.startswith("done")


def test_stuck_ends_the_task_without_prompting_anyone(desk):
    a, out = run([("CLICK", BUTTON, "")] * 3 + [("CLICK", BUTTON, "")], done_p=0.1, allow_risky=True)
    assert out.startswith("blocked: Jev is stuck")
    assert len(desk) == 3


def test_risky_click_asks_and_a_no_stops(desk):
    asked = []
    a, out = run([("CLICK", BUTTON, "")], confirm=lambda m: asked.append(m) or False)
    assert asked and "Send" in asked[0]
    assert out.startswith("stopped: user declined")
    assert desk == []


def test_corner_abort_stops_the_whole_task(desk, monkeypatch):
    def corner(*a, **k):
        raise actions.pyautogui.FailSafeException("corner")
    monkeypatch.setattr(actions, "type_text", corner)
    a, out = run([("TYPE", FIELD, "hello"), ("TYPE", FIELD, "again")])
    assert out.startswith("stopped: mouse moved into a screen corner")


def test_confirmations_decline_without_a_terminal(monkeypatch, capsys):
    monkeypatch.setattr(agent.sys, "stdin", None)
    assert agent.ask_user("Send it?") is False
