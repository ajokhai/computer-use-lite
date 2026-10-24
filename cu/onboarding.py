"""First-run setup: one window listing the permissions Computer Use needs, each with its live
status and a button that asks for it, so nobody has to work out which one is missing.

It opens on first launch and at launch whenever a required permission is missing, and from
Settings › Set Up Permissions…."""

import subprocess

import objc
from AppKit import NSApplication, NSColor, NSMakeRect
from Foundation import NSObject, NSTimer

from . import integration, ui

PAD = 24.0
CARD_W, CARD_H = 262.0, 150.0
WIDTH = 2 * PAD + 2 * CARD_W + ui.GAP
NEXT_H = 112.0

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
        grid_h = 2 * CARD_H + ui.GAP
        height = 132 + grid_h + ui.GAP + NEXT_H + 76
        self.panel = ui.window("Set Up Computer Use", WIDTH, height)
        self.panel.setDelegate_(self)
        content = self.panel.contentView()
        y = ui.header(content, "Let’s get Computer Use ready",
                      "macOS asks you to allow a few things before an app can use your Mac for you. "
                      "Click Allow on each; its card turns to Allowed as soon as it’s on.", PAD, WIDTH, height)

        for i, (key, name, required, desc, _check, _ask) in enumerate(PERMISSIONS):
            col, line = i % 2, i // 2
            frame = NSMakeRect(PAD + col * (CARD_W + ui.GAP), y - (line + 1) * CARD_H - line * ui.GAP, CARD_W, CARD_H)
            self.rows[key] = self._card(content, frame, name, required, desc, key)

        nxt = ui.card(NSMakeRect(PAD, 76, WIDTH - 2 * PAD, NEXT_H))
        content.addSubview_(nxt)
        v, w = nxt.contentView(), WIDTH - 2 * PAD
        self.keys = ui.capsule_button("Add a Model Key…", self, "keysClicked:")
        ui.place(v, ui.label("No API key needed", 15, weight=0.4), 18, NEXT_H - 36)
        p = ui.pill("Next Step", NSColor.controlAccentColor())
        ui.place(v, p, w - 18 - p.frame().size.width, NEXT_H - 34, p.frame().size.width, p.frame().size.height)
        ui.place(v, ui.label("Connect AI Apps in the menu lets the AI you're signed into (Claude, Cursor, "
                             "VS Code…) drive your Mac; it needs these permissions too. Or add a key for the "
                             "bar's own agent.", 12, NSColor.secondaryLabelColor(), wrap=True),
                 18, 14, w - 36 - self.keys.frame().size.width - 16, 56)
        ui.place(v, self.keys, w - 18 - self.keys.frame().size.width, 18, self.keys.frame().size.width, 30)

        self.done = ui.capsule_button("Finish Later", self, "doneClicked:", primary=True)
        self.done.setKeyEquivalent_("\r")
        dw = self.done.frame().size.width
        ui.place(content, self.done, WIDTH - PAD - dw, 24, dw, 30)
        self.relaunch = ui.capsule_button("Relaunch Computer Use", self, "relaunchClicked:")
        self.relaunch.setToolTip_("macOS applies Screen Recording after the app reopens")
        rw = self.relaunch.frame().size.width
        ui.place(content, self.relaunch, WIDTH - PAD - dw - 12 - rw, 24, rw, 30)
        self.refresh()

    @objc.python_method
    def _card(self, content, frame, name, required, desc, key):
        c = ui.card(frame)
        content.addSubview_(c)
        v, pad = c.contentView(), 18.0
        ui.place(v, ui.label(name, 15, weight=0.4), pad, CARD_H - 36)
        detail = ui.place(v, ui.label(desc, 12, NSColor.secondaryLabelColor(), wrap=True), pad, 54, CARD_W - 2 * pad, 48)
        button = ui.capsule_button("Allow", self, "allowClicked:", primary=True)
        button.setIdentifier_(key)
        ui.place(v, button, CARD_W - pad - button.frame().size.width, 18, button.frame().size.width, 30)
        return {"view": v, "pill": None, "state": None, "button": button, "required": required,
                "detail": detail, "desc": desc}

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
            state = ("Allowed", NSColor.systemGreenColor()) if ok else \
                ("Needed", NSColor.systemOrangeColor()) if required else ("Optional", NSColor.tertiaryLabelColor())
            if state[0] != row["state"]:  # rebuild the pill only when it changes (this runs every second)
                row["pill"] = ui.swap_pill(row["view"], row["pill"], *state, CARD_W - 18, CARD_H - 34)
                row["state"] = state[0]
            row["button"].setHidden_(ok)
            all_required &= ok or not required
        # Screen Recording only takes effect after a relaunch, so offer one once it's been asked for.
        screen_ok = self.rows["screen"]["button"].isHidden()
        self.relaunch.setHidden_(screen_ok or not self.asked_screen or integration.app_bundle() is None)
        title = "Done" if all_required else "Finish Later"
        if str(self.done.title()) != title:
            ui.retitle(self.done, title)
            f, r = self.done.frame(), self.relaunch.frame()  # keep Relaunch just left of Done
            self.relaunch.setFrameOrigin_((f.origin.x - 12 - r.size.width, r.origin.y))
        # Still off after asking: most likely a leftover entry from an older build. Say so in its card.
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

