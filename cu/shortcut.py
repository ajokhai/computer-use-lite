"""The one global shortcut that opens Computer Use: a double-tapped modifier (⌃⌃), or a key
with or without modifiers (F5, ⌥Space, ⌘⇧K). Plain logic, no AppKit, so it's testable with
made-up events; menubar.py feeds it real ones."""

from dataclasses import dataclass

# NSEvent modifier flags (device independent)
SHIFT, CTRL, OPT, CMD, FN = 1 << 17, 1 << 18, 1 << 19, 1 << 20, 1 << 23
CHORD_MODS = CTRL | OPT | SHIFT | CMD  # what must match exactly for a key shortcut
ORDER = (CTRL, OPT, SHIFT, CMD)  # Apple's display order
SYMBOL = {CTRL: "⌃", OPT: "⌥", SHIFT: "⇧", CMD: "⌘", FN: "fn"}
NAME = {CTRL: "Control", OPT: "Option", SHIFT: "Shift", CMD: "Command", FN: "Fn"}

FUNCTION_KEYS = {122: "F1", 120: "F2", 99: "F3", 118: "F4", 96: "F5", 97: "F6", 98: "F7", 100: "F8",
                 101: "F9", 109: "F10", 103: "F11", 111: "F12"}
SPECIAL_KEYS = {49: "Space", 36: "↩", 48: "⇥", 51: "⌫", 53: "⎋", 123: "←", 124: "→", 125: "↓", 126: "↑"}

DOUBLE_TAP_WINDOW = 0.45  # seconds between the two taps
MAX_TAP = 0.35  # a press held longer than this is a hold, not a tap


@dataclass(frozen=True)
class Shortcut:
    kind: str  # "double_tap" or "key"
    mods: int  # the tapped modifier, or the modifiers held with the key
    key_code: int | None = None
    key_label: str = ""

    @property
    def label(self) -> str:
        """Compact form for menus, e.g. "⌃⌃", "F5", "⌥Space"."""
        if self.kind == "double_tap":
            return SYMBOL[self.mods] * 2
        return "".join(SYMBOL[m] for m in ORDER if self.mods & m) + self.key_label

    @property
    def title(self) -> str:
        """Spelled out, e.g. "Double-tap Control"."""
        if self.kind == "double_tap":
            return f"Double-tap {NAME[self.mods]}"
        return self.label

    def to_setting(self) -> dict:
        return {"kind": self.kind, "mods": self.mods, "key_code": self.key_code, "key_label": self.key_label}

    @staticmethod
    def from_setting(value) -> "Shortcut":
        """From settings.json, including the strings older versions stored."""
        if isinstance(value, dict):
            try:
                s = Shortcut(value["kind"], int(value["mods"]), value.get("key_code"), value.get("key_label", ""))
                if s.kind == "double_tap" and s.mods in SYMBOL or s.kind == "key" and s.key_code is not None:
                    return s
            except (KeyError, TypeError, ValueError):
                pass
            return DEFAULT
        legacy = {"control": CTRL, "option": OPT, "command": CMD, "shift": SHIFT, "fn": FN, "control_option": CTRL}
        if isinstance(value, str):
            v = value.strip().lower()
            if v in legacy:
                return Shortcut("double_tap", legacy[v])
            for code, name in FUNCTION_KEYS.items():
                if v == name.lower():
                    return Shortcut("key", 0, code, name)
        return DEFAULT

    @staticmethod
    def from_key(key_code: int, flags: int, chars: str = "") -> "Shortcut | None":
        """A recorded key press. Plain keys other than function keys need a modifier,
        so the shortcut can't swallow ordinary typing."""
        mods = flags & CHORD_MODS
        if key_code in FUNCTION_KEYS:
            return Shortcut("key", mods, key_code, FUNCTION_KEYS[key_code])
        if not mods:
            return None
        label = SPECIAL_KEYS.get(key_code) or (chars or "").upper()
        return Shortcut("key", mods, key_code, label) if label else None


DEFAULT = Shortcut("double_tap", CTRL)
PRESETS = [
    DEFAULT,
    Shortcut("double_tap", OPT),
    Shortcut("double_tap", CMD),
    Shortcut("double_tap", SHIFT),
    Shortcut("double_tap", FN),
    Shortcut("key", OPT, 49, "Space"),
    Shortcut("key", CTRL | OPT, 49, "Space"),
    Shortcut("key", 0, 96, "F5"),
]


class Detector:
    """Feed it key events; it says when the shortcut fires. Times are in seconds."""

    def __init__(self, shortcut: Shortcut):
        self.shortcut = shortcut
        self._down_at = None  # when the tapped modifier went down
        self._last_tap = None  # when the previous complete tap ended

    def key_down(self, key_code: int, flags: int, t: float) -> bool:
        """True if this key press is the shortcut (the caller should swallow it)."""
        self._down_at = self._last_tap = None  # typing between taps cancels a double tap
        s = self.shortcut
        return s.kind == "key" and key_code == s.key_code and (flags & CHORD_MODS) == s.mods

    def flags_changed(self, flags: int, t: float) -> bool:
        """True when the second quick tap of the modifier goes down."""
        s = self.shortcut
        if s.kind != "double_tap":
            return False
        held = flags & (CHORD_MODS | FN)
        if held == s.mods:  # exactly our modifier, alone
            if self._down_at is None:
                if self._last_tap is not None and t - self._last_tap <= DOUBLE_TAP_WINDOW:
                    self._last_tap = None
                    return True
                self._down_at = t
            return False
        if held == 0 and self._down_at is not None:  # released: was it a quick tap?
            self._last_tap = t if t - self._down_at <= MAX_TAP else None
        else:  # another modifier joined in: not a tap
            self._last_tap = None
        self._down_at = None
        return False
