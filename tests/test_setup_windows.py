"""The restyled setup window and shortcut recorder, built headless: each permission card's pill and
Allow button follow the live checks, the stale Accessibility hint and Relaunch appear when they
should, Done replaces Finish Later, and the recorder's Save is enabled only with a shortcut."""

import pytest
from AppKit import NSButton, NSTextField

from cu import integration, onboarding
from cu.shortcut import DEFAULT
from cu.shortcut_recorder import ShortcutRecorder


def texts(view) -> list[str]:
    out = []
    for sub in view.subviews():
        if sub.isKindOfClass_(NSTextField):
            out.append(str(sub.stringValue()))
        elif sub.isKindOfClass_(NSButton) and not sub.isHidden():
            out.append(str(sub.title()))
        out += texts(sub)
    return out


@pytest.fixture
def perms(monkeypatch):
    state = {"accessibility": False, "screen": False, "microphone": True, "speech": False}
    monkeypatch.setattr(onboarding, "PERMISSIONS",
                        [(k, t, r, d, (lambda k=k: state[k]), a) for k, t, r, d, _c, a in onboarding.PERMISSIONS])
    monkeypatch.setattr(integration, "app_bundle", lambda: "/Applications/Computer Use.app")
    return state


def card_texts(o, key):
    return texts(o.rows[key]["view"])


def test_cards_follow_the_permission_checks(perms):
    o = onboarding.Onboarding.create(None)
    assert "Needed" in card_texts(o, "accessibility") and "Allow" in card_texts(o, "accessibility")
    assert "Allowed" in card_texts(o, "microphone") and "Allow" not in card_texts(o, "microphone")
    assert "Optional" in card_texts(o, "speech")
    assert str(o.done.title()) == "Finish Later" and o.relaunch.isHidden()

    perms.update(accessibility=True, screen=True)
    o.refresh()
    assert "Allowed" in card_texts(o, "accessibility") and "Allow" not in card_texts(o, "accessibility")
    assert str(o.done.title()) == "Done"
    assert len([t for t in card_texts(o, "accessibility") if t in ("Allowed", "Needed")]) == 1  # pill replaced


def test_stale_accessibility_hint_and_relaunch(perms):
    o = onboarding.Onboarding.create(None)
    o.asked_ax, o.asked_screen = 1, True
    o.refresh()
    assert onboarding.AX_STALE_HINT in card_texts(o, "accessibility")
    assert not o.relaunch.isHidden()
    assert o.relaunch.frame().origin.x + o.relaunch.frame().size.width < o.done.frame().origin.x  # left of Done


def test_recorder_save_needs_a_shortcut():
    r = ShortcutRecorder.create(DEFAULT, lambda s: None)
    assert not r.save.isEnabled() and r.save.alphaValue() < 1
    r._set(DEFAULT)
    assert r.save.isEnabled() and r.save.alphaValue() == 1
    assert str(r.shown.stringValue()) == DEFAULT.title
