"""The floating bar where you ask Computer Use to do something, by typing or by voice.

One row: mic, the request, the mode, send. It uses the system's light or dark appearance,
opens on the screen with the pointer, and closes when you click elsewhere (unless it's
listening). Return sends, Esc stops listening or closes, and Up/Down step through recent tasks.
"""

import objc
from AppKit import (
    NSApplication,
    NSTextFieldCell,
    NSBezierPath,
    NSButton,
    NSColor,
    NSEvent,
    NSFloatingWindowLevel,
    NSFocusRingTypeNone,
    NSFont,
    NSImage,
    NSImageSymbolConfiguration,
    NSImageView,
    NSMakeRect,
    NSPanel,
    NSPopUpButton,
    NSScreen,
    NSTextField,
    NSView,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
    NSWorkspace,
)
from Foundation import NSObject, NSMakeSize
from Quartz import CGColorCreateGenericRGB
from PyObjCTools import AppHelper

from . import ui
from .config import load_settings, save_settings

WIDTH, HEIGHT, RADIUS = 720.0, 64.0, 20.0
SEND = 36.0  # the send button: a rounded square, like the reference bars
BOTTOM = 0.10  # how far up the screen the bar sits, as a fraction of its height
MODES = [("Auto", "auto", "Best your keys and quota allow"),
         ("Vision + Jev", "hybrid", "A vision model plans, Jev clicks"),
         ("Jev", "jev", "Jev only: cheapest, types only text from your request"),
         ("Vision", "vision", "A vision model does everything")]
MAX_RECENT = 20
PLACEHOLDER = "What can I do for you?"

_LINE_BREAK_TRUNCATE_HEAD = 3  # keep the end of a long transcript visible


class _BarFieldCell(NSTextFieldCell):
    # Keep the field transparent while editing, too (the field editor draws its own background).
    def setUpFieldEditorAttributes_(self, editor):
        editor = objc.super(_BarFieldCell, self).setUpFieldEditorAttributes_(editor)
        editor.setDrawsBackground_(False)
        return editor


class _BarPanel(NSPanel):
    # Borderless panels can't take keyboard focus unless they say so.
    def canBecomeKeyWindow(self):
        return True

    def canBecomeMainWindow(self):
        return False


def _symbol(name: str, size: float, weight: str = "regular") -> NSImage:
    weights = {"regular": 0.0, "medium": 0.23, "semibold": 0.3}
    img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
    cfg = NSImageSymbolConfiguration.configurationWithPointSize_weight_(size, weights[weight])
    return img.imageWithSymbolConfiguration_(cfg)


def _icon_button(symbol: str, size: float, action: str, target, tip: str) -> NSButton:
    b = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 28, 28))
    b.setBordered_(False)
    b.setImage_(_symbol(symbol, size))
    b.setImagePosition_(1)  # image only
    b.setContentTintColor_(NSColor.secondaryLabelColor())
    b.setToolTip_(tip)
    b.setTarget_(target)
    b.setAction_(action)
    return b


class PromptBar(NSObject):
    """Owned by the menu bar app, which handles what the bar asks for: start(task),
    start_listening(), stop_listening() and set_mode(mode)."""

    def initWithApp_(self, app):
        self = objc.super(PromptBar, self).init()
        if self is None:
            return None
        self.app = app
        self.listening = False
        self.previous_app = None
        self._recent_index = None
        self._build()
        return self

    @objc.python_method
    @classmethod
    def create(cls, app=None):
        return cls.alloc().initWithApp_(app)

    # --- layout --------------------------------------------------------------

    @objc.python_method
    def _build(self):
        style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        self.panel = _BarPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, WIDTH, HEIGHT), style, 2, False)
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(NSColor.clearColor())
        self.panel.setHasShadow_(True)
        self.panel.setLevel_(NSFloatingWindowLevel)
        self.panel.setHidesOnDeactivate_(False)
        self.panel.setDelegate_(self)

        # A solid card in the app's look (cu/ui.py), with a hairline edge and the window's soft shadow.
        bg = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, WIDTH, HEIGHT))
        bg.setWantsLayer_(True)
        bg.layer().setCornerRadius_(RADIUS)
        bg.layer().setMasksToBounds_(True)
        bg.layer().setBorderWidth_(1.0)
        self.panel.setContentView_(bg)
        self._paint()

        mid = HEIGHT / 2
        glyph = NSImageView.alloc().initWithFrame_(NSMakeRect(18, mid - 13, 26, 26))
        glyph.setImage_(_symbol("cursorarrow.rays", 20, "medium"))
        glyph.setContentTintColor_(ui.dynamic(*(ui._rgb(ui.WARM[0]),) * 2))
        bg.addSubview_(glyph)

        self.send = NSButton.alloc().initWithFrame_(NSMakeRect(WIDTH - 14 - SEND, mid - SEND / 2, SEND, SEND))
        self.send.setBordered_(False)
        self.send.setWantsLayer_(True)
        self.send.layer().setCornerRadius_(10)
        self.send.setImage_(_symbol("arrow.up", 16, "semibold"))
        self.send.setImagePosition_(1)  # image only
        self.send.setToolTip_("Run (Return)")
        self.send.setTarget_(self)
        self.send.setAction_("sendClicked:")
        bg.addSubview_(self.send)

        self.mic = _icon_button("mic", 17, "micClicked:", self, "Speak your request")
        self.mic.setFrameOrigin_((WIDTH - 14 - SEND - 8 - 28, mid - 14))
        bg.addSubview_(self.mic)

        self.mode = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(0, 0, 80, 24), False)
        self.mode.setBordered_(False)
        self.mode.setFont_(NSFont.systemFontOfSize_(14))
        self.mode.setContentTintColor_(NSColor.secondaryLabelColor())
        for title, key, tip in MODES:
            self.mode.addItemWithTitle_(title)
            item = self.mode.lastItem()
            item.setRepresentedObject_(key)
            item.setToolTip_(tip)
        self.mode.setTarget_(self)
        self.mode.setAction_("modeChosen:")
        self.mode.sizeToFit()
        self.mode.cell().setAlignment_(2)  # right: keep the title next to its chevrons
        mode_w = self.mode.frame().size.width
        self.mode.setToolTip_("Which model does the work")
        self.mode.setFrameOrigin_((WIDTH - 14 - SEND - 8 - 28 - 8 - mode_w, mid - 12))
        bg.addSubview_(self.mode)
        right = 14 + SEND + 8 + 28 + 8 + mode_w + 12

        field_x = 18 + 26 + 12
        field_w = WIDTH - field_x - right
        self.field = NSTextField.alloc().initWithFrame_(NSMakeRect(field_x, mid - 13, field_w, 26))
        self.field.setCell_(_BarFieldCell.alloc().initTextCell_(""))
        self.field.setEditable_(True)
        self.field.setBezeled_(False)
        self.field.setBordered_(False)
        self.field.setDrawsBackground_(False)
        self.field.setBackgroundColor_(NSColor.clearColor())  # the editing clip view paints this
        self.field.setFocusRingType_(NSFocusRingTypeNone)
        self.field.setFont_(NSFont.systemFontOfSize_(18))
        self.field.setTextColor_(NSColor.labelColor())
        self.field.setPlaceholderString_(PLACEHOLDER)
        self.field.cell().setLineBreakMode_(_LINE_BREAK_TRUNCATE_HEAD)
        self.field.cell().setUsesSingleLineMode_(True)
        self.field.setDelegate_(self)
        bg.addSubview_(self.field)

        self.set_mode(load_settings().get("mode", "auto"))
        self._update_send()

    @objc.python_method
    def _paint(self):
        """Layer colours are fixed CGColors, so resolve them for the current appearance."""
        bg = self.panel.contentView()
        dark = "Dark" in str(NSApplication.sharedApplication().effectiveAppearance().name())
        bg.layer().setBackgroundColor_(CGColorCreateGenericRGB(*((0.122, 0.122, 0.133) if dark else (1, 1, 1)), 1))
        bg.layer().setBorderColor_(CGColorCreateGenericRGB(*((1, 1, 1, 0.10) if dark else (0, 0, 0, 0.08))))

    # --- showing -------------------------------------------------------------

    @objc.python_method
    def is_visible(self) -> bool:
        return bool(self.panel.isVisible())

    @objc.python_method
    def show(self, previous_app=None):
        """Open at the bottom center of the screen with the pointer, ready to type."""
        self.previous_app = previous_app or NSWorkspace.sharedWorkspace().frontmostApplication()
        mouse = NSEvent.mouseLocation()
        screen = next((s for s in NSScreen.screens() if _contains(s.frame(), mouse)), NSScreen.mainScreen())
        if screen is not None:
            v = screen.visibleFrame()
            self.panel.setFrameOrigin_((v.origin.x + (v.size.width - WIDTH) / 2,
                                        v.origin.y + max(48.0, v.size.height * BOTTOM)))
        self._recent_index = None
        self.set_mode(load_settings().get("mode", "auto"))
        self._paint()
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.panel.makeKeyAndOrderFront_(None)
        self.panel.makeFirstResponder_(self.field)

    @objc.python_method
    def hide(self, restore_focus: bool = True):
        if not self.panel.isVisible():
            return
        self.set_listening(False)
        self.panel.orderOut_(None)
        self.field.setStringValue_("")
        self._update_send()
        if restore_focus and self.previous_app is not None:
            self.previous_app.activateWithOptions_(0)  # the task runs on the app you were in

    # --- listening -----------------------------------------------------------

    @objc.python_method
    def set_listening(self, on: bool, hint: str = ""):
        """Show whether the mic is open. `hint` names the shortcut that sends."""
        self.listening = on
        self.mic.setImage_(_symbol("mic.fill" if on else "mic", 17))
        self.mic.setContentTintColor_(NSColor.systemRedColor() if on else NSColor.secondaryLabelColor())
        self.mic.setToolTip_("Stop listening" if on else "Speak your request")
        if on:
            self._set_text("")
            self.field.setPlaceholderString_("Listening…")
        else:
            self.field.setPlaceholderString_(PLACEHOLDER)
        self._update_send()

    @objc.python_method
    def show_transcript(self, text: str, final: bool = False):
        """Live words while listening; a final transcript is sent after a beat to read it."""
        if not self.panel.isVisible():
            return
        self._set_text(text)
        if final and text.strip():
            AppHelper.callLater(0.4, self._submit)

    # --- modes and history ---------------------------------------------------

    @objc.python_method
    def set_mode(self, mode: str):
        for i in range(self.mode.numberOfItems()):
            if self.mode.itemAtIndex_(i).representedObject() == mode:
                self.mode.selectItemAtIndex_(i)

    @objc.python_method
    def _recent(self) -> list[str]:
        return list(load_settings().get("recent_tasks") or [])

    @objc.python_method
    @staticmethod
    def remember(task: str):
        s = load_settings()
        recent = [t for t in (s.get("recent_tasks") or []) if t != task]
        s["recent_tasks"] = [task, *recent][:MAX_RECENT]
        save_settings(s)

    @objc.python_method
    def _step_recent(self, delta: int):
        recent = self._recent()
        if not recent:
            return
        i = -1 if self._recent_index is None else self._recent_index
        i = max(-1, min(len(recent) - 1, i + delta))
        self._recent_index = None if i < 0 else i
        self._set_text("" if i < 0 else recent[i])

    # --- actions ---------------------------------------------------------------

    @objc.python_method
    def _set_text(self, text: str):
        """Replace the text with the caret at the end (setting it while editing selects it all)."""
        self.field.setStringValue_(text)
        editor = self.field.currentEditor()
        if editor is not None:
            editor.setSelectedRange_((len(text), 0))
        self._update_send()

    @objc.python_method
    def _submit(self):
        text = self.field.stringValue().strip()
        if not text:
            return
        self.remember(text)
        self.hide()
        if self.app is not None:
            self.app.start(text)

    @objc.python_method
    def _update_send(self):
        has_text = bool(self.field.stringValue().strip())
        self.send.setEnabled_(has_text)
        warm = ui._rgb(ui.WARM[0])
        fill = CGColorCreateGenericRGB(warm.redComponent(), warm.greenComponent(), warm.blueComponent(), 1) \
            if has_text else CGColorCreateGenericRGB(0.5, 0.5, 0.5, 0.18)
        self.send.layer().setBackgroundColor_(fill)
        self.send.setContentTintColor_(NSColor.whiteColor() if has_text else NSColor.tertiaryLabelColor())

    def micClicked_(self, sender):
        if self.app is None:
            self.set_listening(not self.listening)
        elif self.listening:
            self.app.stop_listening()
        else:
            self.app.start_listening()

    def sendClicked_(self, sender):
        self._submit()

    def modeChosen_(self, sender):
        mode = sender.selectedItem().representedObject()
        if self.app is not None:
            self.app.set_mode(mode)

    # NSTextField delegate
    def controlTextDidChange_(self, note):
        # Only typing fires this (not text the bar sets), so the user typing takes over from the mic.
        self._recent_index = None
        if self.listening and self.app is not None:
            self.app.stop_listening(send=False)
        self._update_send()

    def control_textView_doCommandBySelector_(self, control, view, selector):
        if selector == "insertNewline:":
            self._submit()
        elif selector == "cancelOperation:":  # Esc
            if self.listening and self.app is not None:
                self.app.stop_listening(send=False)
            else:
                self.hide()
        elif selector == "moveUp:":
            self._step_recent(+1)
        elif selector == "moveDown:":
            self._step_recent(-1)
        else:
            return False
        return True

    # NSWindow delegate: clicking elsewhere closes the bar, unless it's listening.
    def windowDidResignKey_(self, note):
        if not self.listening:
            self.hide(restore_focus=False)


def _contains(frame, point) -> bool:
    return (frame.origin.x <= point.x < frame.origin.x + frame.size.width
            and frame.origin.y <= point.y < frame.origin.y + frame.size.height)
