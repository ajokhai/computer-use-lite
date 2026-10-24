"""A small window that records the shortcut for opening Computer Use: press a key combination,
or double-tap a modifier. It only offers shortcuts the menu bar app can actually detect."""

import time

import objc
from AppKit import (
    NSApplication,
    NSColor,
    NSEvent,
    NSEventMaskFlagsChanged,
    NSEventMaskKeyDown,
    NSMakeRect,
)
from Foundation import NSObject

from . import ui
from .shortcut import CHORD_MODS, DOUBLE_TAP_WINDOW, FN, MAX_TAP, SYMBOL, Shortcut

WIDTH, HEIGHT = 440.0, 330.0
PAD = 24.0
_RETURN, _ESCAPE = 36, 53


class ShortcutRecorder(NSObject):
    def initWithCurrent_onSave_(self, current: Shortcut, on_save):
        self = objc.super(ShortcutRecorder, self).init()
        if self is None:
            return None
        self.on_save = on_save
        self.recorded = None
        self.monitors = []
        self._mod_down = None  # (modifier, time pressed)
        self._last_tap = None  # (modifier, time released)
        self._build(current)
        return self

    @objc.python_method
    @classmethod
    def create(cls, current: Shortcut, on_save):
        return cls.alloc().initWithCurrent_onSave_(current, on_save)

    @objc.python_method
    def _build(self, current: Shortcut):
        self.panel = ui.window("Computer Use Shortcut", WIDTH, HEIGHT)
        self.panel.setDelegate_(self)
        content = self.panel.contentView()
        y = ui.header(content, "Shortcut", "Press the shortcut you want, or double-tap a modifier key.",
                      PAD, WIDTH, HEIGHT) + 22

        card_h = y - 76 - ui.GAP
        self.box = ui.card(NSMakeRect(PAD, 76, WIDTH - 2 * PAD, card_h))
        content.addSubview_(self.box)
        v, w = self.box.contentView(), WIDTH - 2 * PAD
        self.shown = ui.label(current.title, 30, weight=0.3)
        self.shown.setAlignment_(1)  # centre
        ui.place(v, self.shown, 16, card_h / 2 - 2, w - 32, 38)
        self.hint = ui.label("Function keys work alone; other keys need ⌘, ⌥, ⌃ or ⇧.", 12,
                             NSColor.secondaryLabelColor(), wrap=True)
        self.hint.setAlignment_(1)
        ui.place(v, self.hint, 16, card_h / 2 - 40, w - 32, 32)

        self.save = ui.capsule_button("Save", self, "saveClicked:", primary=True)
        self.save.setKeyEquivalent_("\r")
        ui.enable(self.save, False)
        cancel = ui.capsule_button("Cancel", self, "cancelClicked:")
        cancel.setKeyEquivalent_("\x1b")
        sw, cw = self.save.frame().size.width, cancel.frame().size.width
        ui.place(content, self.save, WIDTH - PAD - sw, 24, sw, 30)
        ui.place(content, cancel, WIDTH - PAD - sw - 12 - cw, 24, cw, 30)

    @objc.python_method
    def show(self):
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.panel.center()
        self.panel.makeKeyAndOrderFront_(None)
        self._start()

    @objc.python_method
    def close(self):
        self._stop()
        self.panel.orderOut_(None)

    @objc.python_method
    def _set(self, shortcut: Shortcut | None, message: str = ""):
        self.recorded = shortcut
        if shortcut is not None:
            self.shown.setStringValue_(shortcut.title)
            self.shown.setTextColor_(NSColor.labelColor())
        self.hint.setStringValue_(message or "Press Save to use it, or press another shortcut.")
        self.hint.setTextColor_(NSColor.systemOrangeColor() if message else NSColor.secondaryLabelColor())
        ui.enable(self.save, shortcut is not None)

    # --- recording -------------------------------------------------------------

    @objc.python_method
    def _start(self):
        self._stop()

        def on_key(event):
            code = event.keyCode()
            if code in (_RETURN, _ESCAPE) and not (event.modifierFlags() & CHORD_MODS):
                return event  # let Save/Cancel handle plain Return/Esc
            s = Shortcut.from_key(code, event.modifierFlags(), event.charactersIgnoringModifiers() or "")
            if s is None:
                self._set(self.recorded, "That key needs ⌘, ⌥, ⌃ or ⇧ with it, or use a function key.")
            else:
                self._set(s)
            self._mod_down = self._last_tap = None
            return None  # swallow it: nothing else should react while recording

        def on_flags(event):
            held = event.modifierFlags() & (CHORD_MODS | FN)
            now = time.monotonic()
            single = next((m for m in SYMBOL if held == m), None)
            if single is not None and self._mod_down is None:
                last = self._last_tap
                if last and last[0] == single and now - last[1] <= DOUBLE_TAP_WINDOW:
                    self._set(Shortcut("double_tap", single))
                    self._last_tap = None
                else:
                    self._mod_down = (single, now)
            elif held == 0 and self._mod_down is not None:
                mod, t = self._mod_down
                self._last_tap = (mod, now) if now - t <= MAX_TAP else None
                self._mod_down = None
            elif single is None:
                self._mod_down = self._last_tap = None
            return event

        self.monitors = [
            NSEvent.addLocalMonitorForEventsMatchingMask_handler_(NSEventMaskKeyDown, on_key),
            NSEvent.addLocalMonitorForEventsMatchingMask_handler_(NSEventMaskFlagsChanged, on_flags),
        ]

    @objc.python_method
    def _stop(self):
        for m in self.monitors:
            NSEvent.removeMonitor_(m)
        self.monitors = []

    # --- buttons ---------------------------------------------------------------

    def saveClicked_(self, sender):
        chosen = self.recorded
        self.close()
        if chosen is not None:
            self.on_save(chosen)

    def cancelClicked_(self, sender):
        self.close()

    def windowWillClose_(self, note):
        self._stop()


_open = None  # keep the window alive while it's shown


def show_shortcut_recorder(current: Shortcut, on_save):
    global _open
    if _open is not None:
        _open.close()
    _open = ShortcutRecorder.create(current, on_save)
    _open.show()
