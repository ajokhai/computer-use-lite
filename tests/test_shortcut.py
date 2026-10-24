"""Shortcut detection with made-up key events: what fires, what doesn't, and old settings."""

from cu.shortcut import CMD, CTRL, DEFAULT, FN, OPT, SHIFT, Detector, Shortcut


def taps(d: Detector, events):
    """events: (flags, t) pairs for flagsChanged; returns the times it fired."""
    return [t for flags, t in events if d.flags_changed(flags, t)]


def test_double_tap_fires_on_the_second_press():
    d = Detector(Shortcut("double_tap", CTRL))
    assert taps(d, [(CTRL, 0.0), (0, 0.08), (CTRL, 0.25), (0, 0.3)]) == [0.25]


def test_slow_taps_holds_and_chords_dont_fire():
    d = Detector(Shortcut("double_tap", CTRL))
    assert taps(d, [(CTRL, 0.0), (0, 0.1), (CTRL, 0.9), (0, 1.0)]) == []  # too far apart
    assert taps(d, [(CTRL, 2.0), (0, 2.8), (CTRL, 2.9)]) == []  # first press was a hold
    assert taps(d, [(CTRL, 4.0), (CTRL | SHIFT, 4.05), (0, 4.1), (CTRL, 4.2)]) == []  # ⌃⇧ is a chord
    assert taps(d, [(CMD, 5.0), (0, 5.05), (CMD, 5.1)]) == []  # wrong modifier


def test_typing_between_taps_cancels():
    d = Detector(Shortcut("double_tap", CTRL))
    d.flags_changed(CTRL, 0.0)
    d.flags_changed(0, 0.05)
    assert d.key_down(0, 0, 0.1) is False
    assert d.flags_changed(CTRL, 0.2) is False


def test_three_quick_taps_fire_once():
    d = Detector(Shortcut("double_tap", OPT))
    assert taps(d, [(OPT, 0), (0, .05), (OPT, .1), (0, .15), (OPT, .2), (0, .25)]) == [.1]


def test_key_shortcuts_need_exact_modifiers():
    d = Detector(Shortcut("key", OPT, 49, "Space"))
    assert d.key_down(49, OPT, 0) is True
    assert d.key_down(49, OPT | FN, 0) is True  # fn/caps state doesn't matter
    assert d.key_down(49, OPT | CMD, 0) is False
    assert d.key_down(49, 0, 0) is False
    assert Detector(Shortcut("double_tap", CTRL)).key_down(49, OPT, 0) is False


def test_labels():
    assert DEFAULT.label == "⌃⌃" and DEFAULT.title == "Double-tap Control"
    assert Shortcut("key", CMD | SHIFT | CTRL, 40, "K").label == "⌃⇧⌘K"
    assert Shortcut("key", 0, 96, "F5").title == "F5"


def test_recording_rules():
    assert Shortcut.from_key(96, 0) == Shortcut("key", 0, 96, "F5")  # function keys alone are fine
    assert Shortcut.from_key(40, 0, "k") is None  # a bare letter would eat typing
    assert Shortcut.from_key(40, CMD | SHIFT, "k") == Shortcut("key", CMD | SHIFT, 40, "K")
    assert Shortcut.from_key(49, OPT, " ").label == "⌥Space"


def test_settings_round_trip_and_old_values():
    s = Shortcut("key", CMD | OPT, 40, "K")
    assert Shortcut.from_setting(s.to_setting()) == s
    assert Shortcut.from_setting("option") == Shortcut("double_tap", OPT)
    assert Shortcut.from_setting("f6") == Shortcut("key", 0, 97, "F6")
    for junk in (None, "command-shift-k", {"kind": "key"}, 42):
        assert Shortcut.from_setting(junk) == DEFAULT


def test_system_hotkey_registers_and_releases():
    from cu import hotkey
    from cu.shortcut import CMD, CTRL, OPT
    # ⌃⌥⌘F12: unlikely to be taken. Registering never fires anything without a run loop.
    assert hotkey.register(111, CTRL | OPT | CMD, lambda: None) is True
    hotkey.unregister()
    assert hotkey._registered is None
