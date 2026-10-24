"""First-run setup: one window listing the permissions Computer Use needs, each with its live
status and a button that asks for it, so nobody has to work out which one is missing.

It opens on first launch and at launch whenever a required permission is missing, and from
Settings › Set Up Permissions…."""

import subprocess

import objc
from AppKit import (
    NSApplication,
    NSBox,
    NSButton,
    NSColor,
    NSFont,
    NSImage,
    NSImageSymbolConfiguration,
    NSImageView,
    NSMakeRect,
    NSPanel,
    NSTextField,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSObject, NSTimer

from . import integration

WIDTH = 540.0
ROW_H = 64.0
PAD = 24.0

# status values
GRANTED, MISSING, ASKED = "granted", "missing", "asked"


def _ax() -> bool:
    from ApplicationServices import AXIsProcessTrusted
    return bool(AXIsProcessTrusted())


def _ask_ax():
    from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
    AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})  # adds the app to the list and opens it


def _reset_ax():
    """Forget this app's Accessibility entry. An entry left by an older build with a different
    signature shows as switched on but doesn't count, and macOS won't replace it by itself."""
    from Foundation import NSBundle
    bundle_id = NSBundle.mainBundle().bundleIdentifier()
    if bundle_id:
        subprocess.run(["/usr/bin/tccutil", "reset", "Accessibility", bundle_id], capture_output=True)


def _screen() -> bool:
    from Quartz import CGPreflightScreenCaptureAccess
    return bool(CGPreflightScreenCaptureAccess())


def _ask_screen():
    from Quartz import CGRequestScreenCaptureAccess
    CGRequestScreenCaptureAccess()
    integration.open_privacy_settings("ScreenCapture")


def _mic() -> bool:
    from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
    return AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio) == 3


def _ask_mic():
    from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
    if AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio) == 0:  # not asked yet
        AVCaptureDevice.requestAccessForMediaType_completionHandler_(AVMediaTypeAudio, lambda ok: None)
    else:  # asked before and refused: only Settings can change it now
        integration.open_privacy_settings("Microphone")


def _speech() -> bool:
    from Speech import SFSpeechRecognizer
    return SFSpeechRecognizer.authorizationStatus() == 3


def _ask_speech():
    from Speech import SFSpeechRecognizer
    if SFSpeechRecognizer.authorizationStatus() == 0:
        SFSpeechRecognizer.requestAuthorization_(lambda status: None)
    else:
        integration.open_privacy_settings("SpeechRecognition")


# (key, title, required, what it's for, check, ask)
PERMISSIONS = [
    ("accessibility", "Accessibility", True,
     "Lets Computer Use click, type, and read the buttons and text in your apps.", _ax, _ask_ax),
    ("screen", "Screen Recording", True,
     "Lets it see your screen to understand what's there. Nothing is recorded or saved.", _screen, _ask_screen),
    ("microphone", "Microphone", False,
     "For giving instructions by voice.", _mic, _ask_mic),
    ("speech", "Speech Recognition", False,
     "Turns what you say into text, on your Mac.", _speech, _ask_speech),
]


AX_STALE_HINT = "Already on in Settings? That's left from an older version: click Allow again to replace it."


def missing_required() -> list[str]:
    """Titles of required permissions this app doesn't have yet."""
    out = []
    for _key, title, required, _desc, check, _ask in PERMISSIONS:
        try:
            ok = check()
        except Exception:
            ok = False
        if required and not ok:
            out.append(title)
    return out


def _symbol(name: str, color) -> tuple:
    """(image, tint) for a status icon."""
    img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
    img = img.imageWithSymbolConfiguration_(NSImageSymbolConfiguration.configurationWithPointSize_weight_(20, 0.0))
    return img, color


def _label(text: str, size: float, color, weight: float = 0.0) -> NSTextField:
    f = NSTextField.wrappingLabelWithString_(text)
    f.setFont_(NSFont.systemFontOfSize_weight_(size, weight))
    f.setTextColor_(color)
    f.setSelectable_(False)
    return f


class Onboarding(NSObject):
    def initWithApp_(self, app):
        self = objc.super(Onboarding, self).init()
        if self is None:
            return None
        self.app = app  # the menu bar app: for API Keys… and saving settings
        self.rows = {}
        self.asked_screen = False
        self.asked_ax = 0  # times Allow was clicked for Accessibility
        self.timer = None
        self._build()
        return self

    @objc.python_method
    @classmethod
    def create(cls, app=None):
        return cls.alloc().initWithApp_(app)

    @objc.python_method
    def _build(self):
        height = 150 + ROW_H * len(PERMISSIONS) + 130
        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, WIDTH, height), NSWindowStyleMaskTitled | NSWindowStyleMaskClosable, 2, False)
        self.panel.setTitle_("Set Up Computer Use")
        self.panel.setReleasedWhenClosed_(False)
        self.panel.setDelegate_(self)
        content = self.panel.contentView()
        y = height - PAD

        title = _label("Let’s get Computer Use ready", 20, NSColor.labelColor(), 0.3)
        title.setFrame_(NSMakeRect(PAD, y - 26, WIDTH - 2 * PAD, 26))
        content.addSubview_(title)
        y -= 34
        intro = _label("macOS asks you to allow a few things before an app can use your Mac for you. "
                       "Click Allow on each one; its row turns green as soon as it’s on.",
                       13, NSColor.secondaryLabelColor())
        intro.setFrame_(NSMakeRect(PAD, y - 36, WIDTH - 2 * PAD, 36))
        content.addSubview_(intro)
        y -= 56

        for key, name, required, desc, _check, _ask in PERMISSIONS:
            y -= ROW_H
            self.rows[key] = self._row(content, y, name, required, desc, key)

        y -= 14
        sep = NSBox.alloc().initWithFrame_(NSMakeRect(PAD, y, WIDTH - 2 * PAD, 1))
        sep.setBoxType_(2)  # separator
        content.addSubview_(sep)
        y -= 12
        nxt = _label("Optional next steps", 13, NSColor.labelColor(), 0.3)
        nxt.setFrame_(NSMakeRect(PAD, y - 18, WIDTH - 2 * PAD, 18))
        content.addSubview_(nxt)
        y -= 22
        tips = _label("No API key needed: use Connect AI Apps in the menu, and the AI you're signed into in "
                      "Claude, Antigravity, VS Code or Cursor drives your Mac (that app needs Accessibility and "
                      "Screen Recording too). Or add a model key to use the bar's built-in agent.",
                      12, NSColor.secondaryLabelColor())
        tips.setFrame_(NSMakeRect(PAD, y - 46, WIDTH - 2 * PAD, 46))
        content.addSubview_(tips)

        self.done = NSButton.buttonWithTitle_target_action_("Done", self, "doneClicked:")
        self.done.setKeyEquivalent_("\r")
        self.keys = NSButton.buttonWithTitle_target_action_("Add a Model Key…", self, "keysClicked:")
        self.relaunch = NSButton.buttonWithTitle_target_action_("Relaunch Computer Use", self, "relaunchClicked:")
        for b in (self.done, self.keys, self.relaunch):
            b.sizeToFit()
            content.addSubview_(b)
        dw = max(self.done.frame().size.width, 96)
        self.done.setFrame_(NSMakeRect(WIDTH - PAD - dw, 18, dw, 32))
        self.keys.setFrame_(NSMakeRect(PAD, 18, self.keys.frame().size.width, 32))
        rw = self.relaunch.frame().size.width
        self.relaunch.setFrame_(NSMakeRect(WIDTH - PAD - dw - 12 - rw, 18, rw, 32))
        self.relaunch.setToolTip_("macOS applies Screen Recording after the app reopens")
        self.refresh()

    @objc.python_method
    def _row(self, content, y, name, required, desc, key):
        icon = NSImageView.alloc().initWithFrame_(NSMakeRect(PAD, y + ROW_H / 2 - 13, 26, 26))
        content.addSubview_(icon)
        x = PAD + 38
        head = _label(name, 13, NSColor.labelColor(), 0.3)
        head.sizeToFit()
        head.setFrame_(NSMakeRect(x, y + ROW_H - 26, head.frame().size.width, 18))
        content.addSubview_(head)
        tag = _label("Required" if required else "Optional", 11,
                     NSColor.systemOrangeColor() if required else NSColor.tertiaryLabelColor(), 0.23)
        tag.sizeToFit()
        tag.setFrame_(NSMakeRect(x + head.frame().size.width + 8, y + ROW_H - 25, tag.frame().size.width, 16))
        content.addSubview_(tag)
        button = NSButton.buttonWithTitle_target_action_("Allow…", self, "allowClicked:")
        button.setIdentifier_(key)
        button.sizeToFit()
        bw = max(button.frame().size.width, 84)
        button.setFrame_(NSMakeRect(WIDTH - PAD - bw, y + ROW_H / 2 - 16, bw, 32))
        content.addSubview_(button)
        detail = _label(desc, 12, NSColor.secondaryLabelColor())
        detail.setFrame_(NSMakeRect(x, y + 6, WIDTH - x - PAD - bw - 12, 32))
        content.addSubview_(detail)
        return {"icon": icon, "button": button, "required": required, "detail": detail, "desc": desc}

    # --- status ----------------------------------------------------------------

    @objc.python_method
    def refresh(self):
        all_required = True
        for key, _name, required, _desc, check, _ask in PERMISSIONS:
            try:
                ok = check()
            except Exception:
                ok = False
            row = self.rows[key]
            if ok:
                img, color = _symbol("checkmark.circle.fill", NSColor.systemGreenColor())
            elif required:
                img, color = _symbol("exclamationmark.circle.fill", NSColor.systemOrangeColor())
            else:
                img, color = _symbol("circle", NSColor.tertiaryLabelColor())
            row["icon"].setImage_(img)
            row["icon"].setContentTintColor_(color)
            row["button"].setHidden_(ok)
            all_required &= ok or not required
        # Screen Recording only takes effect after a relaunch, so offer one once it's been asked for.
        screen_ok = self.rows["screen"]["button"].isHidden()
        self.relaunch.setHidden_(screen_ok or not self.asked_screen or integration.app_bundle() is None)
        self.done.setTitle_("Done" if all_required else "Finish Later")
        # Still off after asking: most likely a leftover entry from an older build. Say so in its row.
        ax = self.rows["accessibility"]
        stale = not ax["button"].isHidden() and self.asked_ax > 0
        ax["detail"].setStringValue_(AX_STALE_HINT if stale else ax["desc"])
        ax["detail"].setTextColor_(NSColor.systemOrangeColor() if stale else NSColor.secondaryLabelColor())

    def tick_(self, timer):
        self.refresh()

    # --- showing ---------------------------------------------------------------

    @objc.python_method
    def show(self):
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.panel.center()
        self.panel.makeKeyAndOrderFront_(None)
        if self.timer is None:
            self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                1.0, self, "tick:", None, True)

    @objc.python_method
    def close(self):
        if self.timer is not None:
            self.timer.invalidate()
            self.timer = None
        self.panel.orderOut_(None)

    # --- buttons ---------------------------------------------------------------

    def allowClicked_(self, sender):
        key = sender.identifier()
        if key == "screen":
            self.asked_screen = True
        if key == "accessibility":
            self.asked_ax += 1
            if self.asked_ax > 1:  # asked before and still not on: clear a stale entry, then ask afresh
                _reset_ax()
        next(ask for k, *_rest, ask in PERMISSIONS if k == key)()
        self.refresh()

    def keysClicked_(self, sender):
        if self.app is not None:
            self.app.edit_keys(None)

    def relaunchClicked_(self, sender):
        bundle = integration.app_bundle()
        if bundle is None:
            return
        subprocess.Popen(["/bin/sh", "-c", f'sleep 1; /usr/bin/open "{bundle}"'])
        NSApplication.sharedApplication().terminate_(None)

    def doneClicked_(self, sender):
        if self.app is not None:
            self.app.settings["onboarded"] = True
            from .config import save_settings
            save_settings(self.app.settings)
        self.close()

    def windowWillClose_(self, note):
        if self.timer is not None:
            self.timer.invalidate()
            self.timer = None


_open = None


def show(app=None):
    """Show the setup window (one at a time)."""
    global _open
    if _open is None:
        _open = Onboarding.create(app)
    _open.refresh()
    _open.show()

