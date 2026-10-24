"""A small window that records the shortcut for opening Computer Use: press a key combination,
or double-tap a modifier. It only offers shortcuts the menu bar app can actually detect."""

import time

import objc
from AppKit import (
    NSApplication,
    NSBox,
    NSButton,
    NSColor,
    NSEvent,
    NSEventMaskFlagsChanged,
    NSEventMaskKeyDown,
    NSFont,
    NSMakeRect,
    NSPanel,
    NSTextField,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSObject

from .shortcut import CHORD_MODS, DOUBLE_TAP_WINDOW, FN, MAX_TAP, SYMBOL, Shortcut

WIDTH, HEIGHT = 420.0, 220.0
_BOX_CUSTOM = 4
_RETURN, _ESCAPE = 36, 53


def _label(text: str, size: float, color, weight: float = 0.0, align: int = 0) -> NSTextField:
    f = NSTextField.labelWithString_(text)
    f.setFont_(NSFont.systemFontOfSize_weight_(size, weight))
    f.setTextColor_(color)
    f.setAlignment_(align)
    return f


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
        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, WIDTH, HEIGHT), NSWindowStyleMaskTitled | NSWindowStyleMaskClosable, 2, False)
        self.panel.setTitle_("Computer Use Shortcut")
        self.panel.setDelegate_(self)
        self.panel.setReleasedWhenClosed_(False)
        content = self.panel.contentView()
        pad = 20.0

        intro = _label("Press the shortcut you want, or double-tap a modifier key.", 13, NSColor.labelColor())
        intro.setFrame_(NSMakeRect(pad, HEIGHT - 50, WIDTH - 2 * pad, 34))
        intro.setLineBreakMode_(0)  # wrap
        intro.setMaximumNumberOfLines_(2)
        content.addSubview_(intro)

        self.box = NSBox.alloc().initWithFrame_(NSMakeRect(pad, 78, WIDTH - 2 * pad, 64))
        self.box.setBoxType_(_BOX_CUSTOM)
        self.box.setCornerRadius_(10)
        self.box.setBorderWidth_(1)
        self.box.setBorderColor_(NSColor.controlAccentColor())
        self.box.setFillColor_(NSColor.controlBackgroundColor())
        self.box.setTitlePosition_(0)  # no title
        content.addSubview_(self.box)

        self.shown = _label(current.title, 22, NSColor.labelColor(), 0.3, 1)
        self.shown.setFrame_(NSMakeRect(8, 18, WIDTH - 2 * pad - 16, 28))
        self.box.contentView().addSubview_(self.shown)

        self.hint = _label("Function keys work alone; other keys need ⌘, ⌥, ⌃ or ⇧.", 11, NSColor.secondaryLabelColor())
        self.hint.setFrame_(NSMakeRect(pad, 56, WIDTH - 2 * pad, 16))
        content.addSubview_(self.hint)

        self.save = NSButton.buttonWithTitle_target_action_("Save", self, "saveClicked:")
        self.save.setKeyEquivalent_("\r")
        self.save.setEnabled_(False)
        cancel = NSButton.buttonWithTitle_target_action_("Cancel", self, "cancelClicked:")
        cancel.setKeyEquivalent_("\x1b")
        for b in (self.save, cancel):
            b.sizeToFit()
        sw, cw = max(self.save.frame().size.width, 80), max(cancel.frame().size.width, 80)
        self.save.setFrame_(NSMakeRect(WIDTH - pad - sw, 16, sw, 32))
        cancel.setFrame_(NSMakeRect(WIDTH - pad - sw - 12 - cw, 16, cw, 32))
        content.addSubview_(cancel)
        content.addSubview_(self.save)

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
        self.save.setEnabled_(shortcut is not None)

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
