"""Interactive Shortcut Recorder for Computer Use.
Allows the user to click 'Record Shortcut...', press any key, double-tap modifier,
or key combination, preview it live, and click 'Save Shortcut'.
"""

import time
from AppKit import (
    NSApplication,
    NSBezierPath,
    NSButton,
    NSColor,
    NSEvent,
    NSEventMaskFlagsChanged,
    NSEventMaskKeyDown,
    NSFloatingWindowLevel,
    NSFont,
    NSFontWeightBold,
    NSFontWeightRegular,
    NSMakeRect,
    NSPanel,
    NSScreen,
    NSTextField,
    NSView,
    NSWindowStyleMaskBorderless,
)
from Foundation import NSOperationQueue, NSPoint, NSRect

FUNCTION_KEY_CODES = {
    122: ("F1", "f1"),
    120: ("F2", "f2"),
    99: ("F3", "f3"),
    118: ("F4", "f4"),
    96: ("F5", "f5"),
    97: ("F6", "f6"),
    98: ("F7", "f7"),
    100: ("F8", "f8"),
    101: ("F9", "f9"),
    109: ("F10", "f10"),
    103: ("F11", "f11"),
    111: ("F12", "f12"),
}


class RecorderBackgroundView(NSView):
    def drawRect_(self, dirtyRect):
        bounds = self.bounds()
        radius = 16.0
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bounds, radius, radius)
        # Deep dark luxury obsidian
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.10, 0.11, 0.13, 0.96).set()
        path.fill()

        # Subtle white rim highlight
        NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.16).set()
        path.setLineWidth_(1.0)
        path.stroke()


class ShortcutRecorder:
    def __init__(self, on_save_callback):
        self.on_save = on_save_callback
        self.panel = None
        self.key_box_label = None
        self.save_btn = None
        self.recorded_key = None
        self.monitors = []
        self._tap_state = {
            "down_flag": 0,
            "last_release": 0.0,
            "phase": 0,
        }
        self._setup_ui()

    def _setup_ui(self):
        width, height = 460.0, 270.0
        screen = NSScreen.mainScreen()
        frame = screen.frame() if screen else NSRect(NSPoint(0, 0), (1440, 900))
        x = (frame.size.width - width) / 2.0
        y = (frame.size.height - height) / 2.0

        rect = NSMakeRect(x, y, width, height)
        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, NSWindowStyleMaskBorderless, 2, False
        )
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(NSColor.clearColor())
        self.panel.setLevel_(NSFloatingWindowLevel + 70)
        self.panel.setHasShadow_(True)

        bg = RecorderBackgroundView.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))

        # Title
        title = NSTextField.labelWithString_("Record Voice Mode Shortcut")
        title.setFont_(NSFont.systemFontOfSize_weight_(16.0, NSFontWeightBold))
        title.setTextColor_(NSColor.whiteColor())
        title.setFrame_(NSMakeRect(24, height - 42, width - 48, 22))
        bg.addSubview_(title)

        # Subtitle
        sub = NSTextField.labelWithString_("Press any key, double-tap a modifier, or press a shortcut combination.")
        sub.setFont_(NSFont.systemFontOfSize_weight_(12.5, NSFontWeightRegular))
        sub.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.60))
        sub.setFrame_(NSMakeRect(24, height - 68, width - 48, 18))
        bg.addSubview_(sub)

        # Large center recording display box
        box_y = 96.0
        box_h = 76.0
        box_w = width - 48.0
        self.box_view = NSView.alloc().initWithFrame_(NSMakeRect(24, box_y, box_w, box_h))
        self.box_view.setWantsLayer_(True)
        self.box_view.layer().setCornerRadius_(12.0)
        self.box_view.layer().setBackgroundColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.15, 0.16, 0.20, 0.85).CGColor()
        )
        self.box_view.layer().setBorderWidth_(1.5)
        self.box_view.layer().setBorderColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.0, 0.82, 1.0, 0.50).CGColor()
        )

        self.key_box_label = NSTextField.labelWithString_("⌨️  Press keys to record…")
        self.key_box_label.setFont_(NSFont.systemFontOfSize_weight_(17.0, NSFontWeightBold))
        self.key_box_label.setTextColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(0.0, 0.90, 1.0, 1.0))
        self.key_box_label.setAlignment_(1)  # Center
        self.key_box_label.setFrame_(NSMakeRect(10, 24, box_w - 20, 26))
        self.box_view.addSubview_(self.key_box_label)
        bg.addSubview_(self.box_view)

        # Cancel Button
        cancel_btn = NSButton.alloc().initWithFrame_(NSMakeRect(24, 24, 90, 36))
        cancel_btn.setTitle_("Cancel")
        cancel_btn.setBezelStyle_(1)  # Rounded
        cancel_btn.setTarget_(self)
        cancel_btn.setAction_("onCancelAction:")
        bg.addSubview_(cancel_btn)

        # Clear / Re-record Button
        clear_btn = NSButton.alloc().initWithFrame_(NSMakeRect(124, 24, 110, 36))
        clear_btn.setTitle_("Record Again")
        clear_btn.setBezelStyle_(1)
        clear_btn.setTarget_(self)
        clear_btn.setAction_("onClearAction:")
        bg.addSubview_(clear_btn)

        # Save Shortcut Button
        self.save_btn = NSButton.alloc().initWithFrame_(NSMakeRect(width - 154, 24, 130, 36))
        self.save_btn.setTitle_("Save Shortcut")
        self.save_btn.setBezelStyle_(1)
        self.save_btn.setTarget_(self)
        self.save_btn.setAction_("onSaveAction:")
        self.save_btn.setEnabled_(False)
        bg.addSubview_(self.save_btn)

        self.panel.setContentView_(bg)

    def show(self):
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.panel.makeKeyAndOrderFront_(None)
        self._start_listening()

    def close(self):
        self._stop_listening()
        if self.panel:
            self.panel.orderOut_(None)

    def _start_listening(self):
        self._stop_listening()

        def on_key(event):
            code = event.keyCode()
            flags = event.modifierFlags()
            mods = []
            if flags & (1 << 18):
                mods.append("⌃ Control")
            if flags & (1 << 19):
                mods.append("⌥ Option")
            if flags & (1 << 17):
                mods.append("⇧ Shift")
            if flags & (1 << 20):
                mods.append("⌘ Command")

            # Check function keys (F1 - F12)
            if code in FUNCTION_KEY_CODES:
                name, key_id = FUNCTION_KEY_CODES[code]
                label = " + ".join(mods + [name]) if mods else f"Press {name}"
                self._set_recorded(label, key_id)
                return None

            # Normal keys with modifier (e.g. Cmd+Shift+K)
            chars = event.charactersIgnoringModifiers() or ""
            if chars and mods:
                char_disp = chars.upper() if len(chars) == 1 else chars
                if code == 49:
                    char_disp = "Space"
                label = " + ".join(mods + [char_disp])
                key_id = clean_id = "-".join([m.split()[-1].lower() for m in mods] + [char_disp.lower()])
                self._set_recorded(label, key_id)
                return None

            return event

        def on_flags(event):
            flags = event.modifierFlags()
            now = time.monotonic()

            MOD_MAP = [
                (1 << 18, "Control", "control", "⌃"),
                (1 << 19, "Option", "option", "⌥"),
                (1 << 20, "Command", "command", "⌘"),
                (1 << 17, "Shift", "shift", "⇧"),
                (1 << 23, "Fn / Globe", "fn", "🌐"),
            ]

            active = None
            for bit, name, key_id, sym in MOD_MAP:
                if (flags & bit) != 0:
                    active = (bit, name, key_id, sym)
                    break

            if active:
                bit, name, key_id, sym = active
                if self._tap_state["down_flag"] == 0:
                    self._tap_state["down_flag"] = bit
                    if (
                        self._tap_state["phase"] == 1
                        and (now - self._tap_state["last_release"]) <= 0.45
                    ):
                        # Double tap modifier confirmed!
                        label = f"Double-tap {sym} {name}"
                        self._set_recorded(label, key_id)
                        self._tap_state["phase"] = 0
                    else:
                        self._tap_state["phase"] = 1
            else:
                if self._tap_state["down_flag"] != 0:
                    self._tap_state["down_flag"] = 0
                    self._tap_state["last_release"] = now

        self.monitors = [
            NSEvent.addLocalMonitorForEventsMatchingMask_handler_(NSEventMaskKeyDown, on_key),
            NSEvent.addLocalMonitorForEventsMatchingMask_handler_(NSEventMaskFlagsChanged, lambda e: (on_flags(e), e)[1]),
            NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(NSEventMaskKeyDown, on_key),
            NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(NSEventMaskFlagsChanged, on_flags),
        ]

    def _stop_listening(self):
        for m in self.monitors:
            try:
                NSEvent.removeMonitor_(m)
            except Exception:
                pass
        self.monitors = []

    def _set_recorded(self, display_label: str, key_id: str):
        self.recorded_key = key_id

        def _update():
            if self.key_box_label:
                self.key_box_label.setStringValue_(f"✨  {display_label}")
                self.key_box_label.setTextColor_(
                    NSColor.colorWithCalibratedRed_green_blue_alpha_(0.2, 1.0, 0.4, 1.0)
                )
            if self.save_btn:
                self.save_btn.setEnabled_(True)

        NSOperationQueue.mainQueue().addOperationWithBlock_(_update)

    def onClearAction_(self, sender):
        self.recorded_key = None
        self._tap_state = {"down_flag": 0, "last_release": 0.0, "phase": 0}
        self.key_box_label.setStringValue_("⌨️  Press keys to record…")
        self.key_box_label.setTextColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.0, 0.90, 1.0, 1.0)
        )
        self.save_btn.setEnabled_(False)

    def onCancelAction_(self, sender):
        self.close()

    def onSaveAction_(self, sender):
        if self.recorded_key and self.on_save:
            self.on_save(self.recorded_key)
        self.close()


def show_shortcut_recorder(on_save_callback):
    """Factory function to instantiate and display the Shortcut Recorder."""
    recorder = ShortcutRecorder(on_save_callback)
    recorder.show()
    return recorder
