"""Models and Keys statuses, and the Vision Model and Usage menus, built headless with rumps items:
only usable models are offered, and usage is one line per provider in use."""

import pytest
import rumps

from cu import config, keys_window, menubar, usage, vision


def settings(**over):
    s = {**config.DEFAULTS, **over}
    s["limits"] = dict(config.DEFAULTS["limits"])
    return s


@pytest.fixture
def keys(monkeypatch):
    for n in ("TYPESAFE_API_KEY", "JEV_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
              "GOOGLE_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setattr(usage, "status", lambda m: {"requests": 2, "used": 2, "limit": None, "available": True,
                                                    "reset_at": 0, "blocked_until": None})
    monkeypatch.setattr(usage, "summary_lines", lambda ms: [f"{usage.pretty(m)}: 2 requests today" for m in [*ms, "jev"]])

    def use(**s):
        monkeypatch.setattr(vision, "load_settings", lambda: settings(**s))
        return settings(**s)
    return use


def labels(menu) -> list[str]:
    return [("✓ " if v.state else "") + v.title if hasattr(v, "title") else "---" for v in menu.values()]


def app_with(s):
    app = menubar.MenuBar.__new__(menubar.MenuBar)
    app.settings, app.agent = s, None
    app.vision_menu, app.usage_menu = rumps.MenuItem("Vision Model"), rumps.MenuItem("Usage")
    app.set_icon = lambda symbol: None
    return app


def test_statuses(keys, monkeypatch):
    s = keys(openrouter_model="", local_model="")
    assert keys_window.status("claude", s) == (False, "Not set")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("OPENROUTER_API_KEY", "o")
    assert keys_window.status("claude", s) == (True, "Added")
    assert keys_window.status("openrouter", s) == (False, "Key added, no model")
    assert keys_window.status("openrouter", settings(openrouter_model="v/m")) == (True, "v/m")
    assert keys_window.status("local", settings(local_model="llava")) == (True, "llava")


def test_vision_menu_offers_only_usable_models(keys, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    app = app_with(keys(vision_model="claude-sonnet-5-5", local_model="llava"))
    app.build_vision_menu()
    assert labels(app.vision_menu) == ["Auto", "---", "Claude Opus 5.5", "✓ Claude Sonnet 5.5", "Local llava",
                                       "---", "Add a Model…"]


def test_vision_menu_with_nothing_set_up(keys):
    app = app_with(keys())
    app.build_vision_menu()
    assert labels(app.vision_menu) == ["✓ Auto", "---", "Add a Model…"]


def test_usage_is_one_line_per_provider(keys, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("TYPESAFE_API_KEY", "t")
    app = app_with(keys())
    app.refresh_usage()
    top = labels(app.usage_menu)
    assert top == ["Jev: $2.00 this month", "Gemini: 6 requests today", "---", "Add a Model…", "Clear Out-of-Quota Flags"]
    gemini = list(app.usage_menu.values())[1]
    assert labels(gemini)[:3] == ["Gemini 3.8 Flash: 2 requests today", "Gemini 3.5 Flash Lite: 2 requests today",
                                  "Gemini 3 Flash Preview: 2 requests today"]
    assert "Open Dashboard" in labels(gemini) and "Set Gemini 3.8 Flash Daily Limit…" in labels(gemini)
