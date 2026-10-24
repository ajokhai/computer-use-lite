"""The menu bar icon says whether an agent is working: this app's own, or another AI through cu-mcp."""

import json
import os
import time
import types

from cu import menubar


def app(tmp_path, monkeypatch, **state):
    monkeypatch.setattr(menubar, "MCP_LOG", tmp_path / "mcp.log")
    a = menubar.MenuBar.__new__(menubar.MenuBar)
    a.agent, a.prompt_bar, a.listener, a.direct = None, None, None, False
    for k, v in state.items():
        setattr(a, k, v)
    return a


def test_idle_until_an_agent_runs(tmp_path, monkeypatch):
    assert app(tmp_path, monkeypatch).icon_state() == "idle"
    assert app(tmp_path, monkeypatch, agent=object()).icon_state() == "working"


def test_another_ai_using_cu_mcp_counts_as_working(tmp_path, monkeypatch):
    a = app(tmp_path, monkeypatch)
    (tmp_path / "mcp.log").write_text("2026-10-24 18:54:07 click_element {} ok 40ms\n")
    assert a.icon_state() == "working"
    old = time.time() - menubar.MCP_BUSY_SECS - 5
    os.utime(tmp_path / "mcp.log", (old, old))
    a._mcp_checked = 0.0
    assert a.icon_state() == "idle"


def test_listening_shows_when_you_asked_it_to_listen(tmp_path, monkeypatch):
    listener = types.SimpleNamespace(listening=True)
    assert app(tmp_path, monkeypatch, listener=listener, direct=True).icon_state() == "listening"
    assert app(tmp_path, monkeypatch, listener=listener).icon_state() == "idle"  # hands-free waiting isn't shown


def test_escape_three_times_stops_everything(tmp_path, monkeypatch):
    monkeypatch.setattr(menubar, "STOP_REQUEST", tmp_path / "stop-request")
    monkeypatch.setattr(menubar, "_typed_by_an_agent", lambda e: False)
    stopped, said = [], []
    a = app(tmp_path, monkeypatch, agent=object(), status=types.SimpleNamespace(title=""))
    a.stop, a.say = stopped.append, said.append
    a.on_escape(None)
    a.on_escape(None)
    assert not stopped
    a.on_escape(None)
    assert stopped and said == ["Okay, I stopped."] and (tmp_path / "stop-request").exists()


def test_escapes_an_agent_types_dont_count(tmp_path, monkeypatch):
    monkeypatch.setattr(menubar, "_typed_by_an_agent", lambda e: True)
    stopped = []
    a = app(tmp_path, monkeypatch, agent=object())
    a.stop = stopped.append
    for _ in range(5):
        a.on_escape(None)
    assert not stopped


def test_escape_three_times_ends_the_working_state(tmp_path, monkeypatch):
    monkeypatch.setattr(menubar, "STOP_REQUEST", tmp_path / "stop-request")
    a = app(tmp_path, monkeypatch)
    (tmp_path / "mcp.log").write_text("click_element ok\n")
    assert a.icon_state() == "working"
    (tmp_path / "stop-request").write_text(str(time.time() + 1))
    a._mcp_checked = 0.0
    assert a.icon_state() == "idle"


def test_an_ai_app_counts_as_working_for_its_whole_task(tmp_path, monkeypatch):
    monkeypatch.setattr(menubar, "AGENT_TASK", tmp_path / "agent-task")
    monkeypatch.setattr(menubar, "STOP_REQUEST", tmp_path / "stop-request")
    a = app(tmp_path, monkeypatch)
    log = tmp_path / "mcp.log"
    log.write_text("click_element ok\n")
    thinking = time.time() - 200  # its last action was a while ago; it's thinking
    os.utime(log, (thinking, thinking))
    (tmp_path / "agent-task").write_text(json.dumps({"state": "working", "at": thinking}))
    assert a.icon_state() == "working"
    (tmp_path / "agent-task").write_text(json.dumps({"state": "done", "at": time.time()}))
    a._mcp_checked = 0.0
    assert a.icon_state() == "idle"


def test_the_persons_input_is_noted_while_an_agent_works(tmp_path, monkeypatch):
    monkeypatch.setattr(menubar, "USER_INPUT", tmp_path / "user-input")
    a = app(tmp_path, monkeypatch)
    monkeypatch.setattr(menubar, "_typed_by_an_agent", lambda e: False)
    a.on_user_input(None, "click")
    assert not (tmp_path / "user-input").exists()  # nothing is running: nothing to note
    a.agent = object()
    a.on_user_input(None, "click")
    a.on_user_input(None, "key")  # within a second: one note is enough
    assert (tmp_path / "user-input").read_text().split()[1:] == ["click"]
    a._input_noted = 0.0
    monkeypatch.setattr(menubar, "_typed_by_an_agent", lambda e: True)
    a.on_user_input(None, "key")  # the agent's own key
    assert len((tmp_path / "user-input").read_text().splitlines()) == 1
