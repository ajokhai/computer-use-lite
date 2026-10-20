"""Sleek floating talk & prompt bar for Computer Use.

Premium macOS command pill matching modern AI command surfaces:
- Borderless floating pill with deep obsidian frosted glass and crisp rim lighting
- Transparent, un-blocked text input with bright legible placeholder and cyan caret
- Refined typography and vector controls (no cartoon emojis or muddy dark smudges)
- Dedicated mic button with glowing ruby REC state
- Teach Skill button (⌘T)
- Model selector pill dropdown (Auto, Hybrid, Gemini, Jev)
- Up-arrow submit button
"""

import sys
import threading
import time
from typing import Callable, Optional

import objc
from AppKit import (
    NSApplication,
    NSAttributedString,
    NSBezierPath,
    NSButton,
    NSButtonTypeMomentaryPushIn,
    NSColor,
    NSEvent,
    NSEventMaskKeyDown,
    NSFloatingWindowLevel,
    NSFocusRingTypeNone,
    NSFont,
    NSFontAttributeName,
    NSFontWeightBold,
    NSFontWeightMedium,
    NSFontWeightRegular,
    NSForegroundColorAttributeName,
    NSMakeRect,
    NSMenu,
    NSMenuItem,
    NSPanel,
    NSScreen,
    NSTextField,
    NSView,
    NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectMaterialHUDWindow,
    NSVisualEffectView,
    NSWindowStyleMaskBorderless,
    NSWorkspace,
)
from Foundation import NSObject, NSOperationQueue, NSPoint, NSRect, NSSize


class BarBackgroundView(NSView):
    def drawRect_(self, dirtyRect):
        bounds = self.bounds()
        radius = 16.0
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bounds, radius, radius)

        # Deep obsidian luxury glass fill
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.10, 0.11, 0.13, 0.96).set()
        path.fill()

        # Crisp, high-end outer border
        NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.16).set()
        path.setLineWidth_(1.0)
        path.stroke()

        # Subtle top-edge rim lighting highlight
        rim_path = NSBezierPath.bezierPath()
        rim_path.moveToPoint_(NSPoint(radius, bounds.size.height - 0.5))
        rim_path.lineToPoint_(NSPoint(bounds.size.width - radius, bounds.size.height - 0.5))
        rim_path.setLineWidth_(1.0)
        NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.22).set()
        rim_path.stroke()

        # Clean separator between input and toolbar
        sep_y = 38.0
        sep_path = NSBezierPath.bezierPath()
        sep_path.moveToPoint_(NSPoint(14.0, sep_y))
        sep_path.lineToPoint_(NSPoint(bounds.size.width - 14.0, sep_y))
        sep_path.setLineWidth_(0.75)
        NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.08).set()
        sep_path.stroke()


class KeyTextField(NSTextField):
    """NSTextField that intercepts Return, Esc, shortcuts, and maintains seamless transparency."""

    def setBarHandler_(self, handler):
        self._handler = handler

    def setUpFieldEditorAttributes_(self, textObj):
        textObj = objc.super(KeyTextField, self).setUpFieldEditorAttributes_(textObj)
        if textObj:
            textObj.setDrawsBackground_(False)
            textObj.setBackgroundColor_(NSColor.clearColor())
            textObj.setTextColor_(NSColor.whiteColor())
            # Electric cyan insertion point
            textObj.setInsertionPointColor_(
                NSColor.colorWithCalibratedRed_green_blue_alpha_(0.0, 0.85, 1.0, 1.0)
            )
        return textObj

    def performKeyEquivalent_(self, event):
        code = event.keyCode()
        chars = event.characters() or ""
        mods = event.modifierFlags()
        is_cmd = bool(mods & (1 << 20))

        if code == 53 or chars == "\x1b":  # Escape
            if hasattr(self, "_handler") and self._handler:
                self._handler.on_escape()
                return True
        elif code in (36, 76) or chars == "\r":  # Return
            if hasattr(self, "_handler") and self._handler:
                self._handler.on_submit()
                return True
        elif is_cmd and chars.lower() == "t":  # Cmd-T for Teach Skill
            if hasattr(self, "_handler") and self._handler:
                self._handler.on_teach_skill()
                return True
        return objc.super(KeyTextField, self).performKeyEquivalent_(event)


class PillButton(NSButton):
    def drawRect_(self, dirtyRect):
        bounds = self.bounds()
        radius = bounds.size.height / 2.0
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bounds, radius, radius)

        # Translucent glass pill background
        if getattr(self, "_is_active", False):
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.0, 0.55, 0.95, 0.35).set()
        else:
            NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.08).set()
        path.fill()

        # Crisp 1px border
        NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.16).set()
        path.setLineWidth_(1.0)
        path.stroke()

        objc.super(PillButton, self).drawRect_(dirtyRect)


class PromptBar:
    """Floating bottom prompt bar controller."""

    def __init__(self, menubar_app=None):
        self.menubar = menubar_app
        self.panel = None
        self.input_field = None
        self.voice_btn = None
        self.model_pill = None
        self.teach_btn = None
        self.history_btn = None
        self.submit_btn = None
        self.mode_label = None
        self.previous_app = None
        self.is_voice_mode = False
        self.width = 680.0
        self.height = 84.0
        self._build_panel()

    def _build_panel(self):
        screens = NSScreen.screens()
        screen = NSScreen.mainScreen() or (screens[0] if screens else None)
        if screen:
            visible = screen.visibleFrame()
            x = visible.origin.x + (visible.size.width - self.width) / 2.0
            y = visible.origin.y + 45.0
        else:
            x, y = 300.0, 100.0

        rect = NSMakeRect(x, y, self.width, self.height)
        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, NSWindowStyleMaskBorderless, 2, False
        )
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(NSColor.clearColor())
        self.panel.setLevel_(NSFloatingWindowLevel + 25)
        self.panel.setHasShadow_(True)
        self.panel.setAcceptsMouseMovedEvents_(True)

        # Background View
        self.bg_view = BarBackgroundView.alloc().initWithFrame_(NSMakeRect(0, 0, self.width, self.height))

        # Top row: Text input (x: 18, y: 44, w: self.width - 92, h: 32)
        self.input_field = KeyTextField.alloc().initWithFrame_(NSMakeRect(18, 42, self.width - 92, 32))
        self.input_field.setBarHandler_(self)
        self.input_field.setBezeled_(False)
        self.input_field.setDrawsBackground_(False)
        self.input_field.setFocusRingType_(NSFocusRingTypeNone)
        self.input_field.setTextColor_(NSColor.whiteColor())
        self.input_field.setFont_(NSFont.systemFontOfSize_weight_(14.5, NSFontWeightRegular))
        self.set_placeholder("Ask Computer Use or type a task... (Esc to dismiss)")
        self.bg_view.addSubview_(self.input_field)

        # Top row: Mic button (x: width - 58, y: 44, w: 44, h: 28)
        self.voice_btn = NSButton.alloc().initWithFrame_(NSMakeRect(self.width - 60, 44, 46, 28))
        self.voice_btn.setTitle_("🎙")
        self.voice_btn.setBordered_(False)
        self.voice_btn.setButtonType_(NSButtonTypeMomentaryPushIn)
        self.voice_btn.setTarget_(self)
        self.voice_btn.setAction_("toggleVoiceAction:")
        self.voice_btn.setToolTip_("Switch to Interactive Voice Mode")
        self.bg_view.addSubview_(self.voice_btn)

        # Bottom row: Action buttons
        # 1. Plus button
        plus_btn = NSButton.alloc().initWithFrame_(NSMakeRect(14, 7, 26, 26))
        plus_btn.setTitle_("＋")
        plus_btn.setBordered_(False)
        plus_btn.setButtonType_(NSButtonTypeMomentaryPushIn)
        plus_btn.setFont_(NSFont.systemFontOfSize_weight_(14.0, NSFontWeightMedium))
        plus_btn.setToolTip_("Actions & Quick Options")
        plus_btn.setTarget_(self)
        plus_btn.setAction_("onPlusAction:")
        self.bg_view.addSubview_(plus_btn)

        # 2. Teach Skill button (replacing yellow cartoon pencil with crisp ✦ Teach)
        self.teach_btn = PillButton.alloc().initWithFrame_(NSMakeRect(44, 7, 72, 25))
        self.teach_btn.setTitle_("✦ Teach")
        self.teach_btn.setBordered_(False)
        self.teach_btn.setButtonType_(NSButtonTypeMomentaryPushIn)
        self.teach_btn.setFont_(NSFont.systemFontOfSize_weight_(11.5, NSFontWeightMedium))
        self.teach_btn.setToolTip_("Teach Skill: demonstrate an action for Computer Use to learn (⌘T)")
        self.teach_btn.setTarget_(self)
        self.teach_btn.setAction_("onTeachAction:")
        self.bg_view.addSubview_(self.teach_btn)

        # 3. History button (replacing dark stopwatch with crisp ↺ Recent)
        self.history_btn = PillButton.alloc().initWithFrame_(NSMakeRect(122, 7, 74, 25))
        self.history_btn.setTitle_("↺ Recent")
        self.history_btn.setBordered_(False)
        self.history_btn.setButtonType_(NSButtonTypeMomentaryPushIn)
        self.history_btn.setFont_(NSFont.systemFontOfSize_weight_(11.5, NSFontWeightMedium))
        self.history_btn.setToolTip_("Recent Tasks")
        self.history_btn.setTarget_(self)
        self.history_btn.setAction_("onHistoryAction:")
        self.bg_view.addSubview_(self.history_btn)

        # 4. Model selector pill button ([Auto ▾])
        self.model_pill = PillButton.alloc().initWithFrame_(NSMakeRect(202, 7, 110, 25))
        mode = self.menubar.settings.get("mode", "auto") if self.menubar else "auto"
        self._update_model_label(mode)
        self.model_pill.setBordered_(False)
        self.model_pill.setButtonType_(NSButtonTypeMomentaryPushIn)
        self.model_pill.setFont_(NSFont.systemFontOfSize_weight_(11.5, NSFontWeightMedium))
        self.model_pill.setTarget_(self)
        self.model_pill.setAction_("onModelPillAction:")
        self.model_pill.setToolTip_("Select reasoning model (Auto, Hybrid, Gemini, Jev)")
        self.bg_view.addSubview_(self.model_pill)

        # Right side: Mode indicator badge (⚡ Auto)
        self.mode_label = NSTextField.alloc().initWithFrame_(NSMakeRect(self.width - 124, 10, 75, 18))
        self.mode_label.setBezeled_(False)
        self.mode_label.setDrawsBackground_(False)
        self.mode_label.setEditable_(False)
        self.mode_label.setSelectable_(False)
        self.mode_label.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.75))
        self.mode_label.setFont_(NSFont.systemFontOfSize_weight_(11.5, NSFontWeightMedium))
        self.mode_label.setStringValue_(f"⚡ {mode.capitalize()}")
        self.bg_view.addSubview_(self.mode_label)

        # Right side: Submit button (↑)
        self.submit_btn = PillButton.alloc().initWithFrame_(NSMakeRect(self.width - 42, 7, 28, 25))
        self.submit_btn.setTitle_("↑")
        self.submit_btn.setBordered_(False)
        self.submit_btn.setButtonType_(NSButtonTypeMomentaryPushIn)
        self.submit_btn.setFont_(NSFont.systemFontOfSize_weight_(14.0, NSFontWeightBold))
        self.submit_btn.setTarget_(self)
        self.submit_btn.setAction_("onSubmitAction:")
        self.submit_btn.setToolTip_("Run Task (Return)")
        self.bg_view.addSubview_(self.submit_btn)

        self.panel.setContentView_(self.bg_view)

    def set_placeholder(self, text: str):
        """Set a crisp, high-contrast silver placeholder that is clearly legible."""
        if not self.input_field:
            return
        attrs = {
            NSForegroundColorAttributeName: NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.52),
            NSFontAttributeName: NSFont.systemFontOfSize_weight_(14.5, NSFontWeightRegular),
        }
        astr = NSAttributedString.alloc().initWithString_attributes_(text, attrs)
        self.input_field.setPlaceholderAttributedString_(astr)

    def _shortcut_hint(self) -> str:
        if self.menubar and hasattr(self.menubar, "get_voice_shortcut_info"):
            info = self.menubar.get_voice_shortcut_info()
            if info.get("type") == "modifier":
                return f"Double-tap {info['symbol']}"
            return f"Press {info['symbol']}"
        return "Double-tap ⌃"

    def _shortcut_holding_hint(self) -> str:
        if self.menubar and hasattr(self.menubar, "get_voice_shortcut_info"):
            info = self.menubar.get_voice_shortcut_info()
            return f"holding {info['short']}"
        return "holding Control"

    def set_voice_mode_state(self, active: bool):
        self.is_voice_mode = active
        if active:
            # Sleek red recording pill, no cartoon emoji
            self.voice_btn.setTitle_("● LIVE")
            self.voice_btn.setFont_(NSFont.boldSystemFontOfSize_(10.5))
            self.input_field.setStringValue_("")
            self.set_placeholder(f"🎙 Active Voice Mode — Speak anytime ({self._shortcut_hint()} to exit)")
        else:
            self.voice_btn.setTitle_("🎙")
            self.voice_btn.setFont_(NSFont.systemFontOfSize_(14.0))
            self.input_field.setStringValue_("")
            self.set_placeholder("Ask Computer Use or type a task... (Esc to dismiss)")

    def set_hold_listening_state(self, holding: bool):
        if holding:
            self.voice_btn.setTitle_("● REC")
            self.voice_btn.setFont_(NSFont.boldSystemFontOfSize_(10.5))
            self.input_field.setStringValue_("")
            self.set_placeholder(f"🎙 Listening ({self._shortcut_holding_hint()})…")
        else:
            self.voice_btn.setTitle_("🎙")
            self.voice_btn.setFont_(NSFont.systemFontOfSize_(14.0))
            self.set_placeholder("Processing utterance…")

    def update_voice_shortcut_hint(self):
        if self.is_voice_mode and self.panel and self.panel.isVisible():
            self.set_placeholder(f"🎙 Listening… ({self._shortcut_hint()} to exit voice mode)")

    def update_speech_transcript(self, text: str, is_final: bool = False):
        if not self.panel or not self.panel.isVisible():
            return
        if is_final:
            self.input_field.setStringValue_(text)
            if not self.is_voice_mode:
                self.voice_btn.setTitle_("🎙")
                threading.Timer(0.3, self.on_submit).start()
            else:
                def _reset_prompt():
                    if self.is_voice_mode and self.panel and self.panel.isVisible():
                        self.input_field.setStringValue_("")
                        self.set_placeholder(f"🎙 Listening… ({self._shortcut_hint()} to exit voice mode)")
                threading.Timer(1.5, _reset_prompt).start()
        else:
            self.input_field.setStringValue_(f"🎙 {text}")

    def onTeachAction_(self, sender):
        self.on_teach_skill()

    def on_teach_skill(self):
        """Enter skill teaching mode."""
        from .memory import get_memory_store, prompt_user_for_help

        front = NSWorkspace.sharedWorkspace().frontmostApplication()
        app_name = front.localizedName() if front else "Mac"

        self.input_field.setStringValue_("")
        self.set_placeholder(f"Teach skill in {app_name}: enter skill name and press Enter...")
        self.panel.makeFirstResponder_(self.input_field)

        original_submit = self.on_submit

        def _do_teach():
            skill_name = self.input_field.stringValue().strip()
            self.on_submit = original_submit
            if not skill_name:
                self.set_placeholder("Ask Computer Use or type a task... (Esc to dismiss)")
                return
            self.hide()
            if self.menubar:
                self.menubar.log(f"\n🎓 Teaching skill: '{skill_name}' in {app_name}")
                threading.Thread(
                    target=prompt_user_for_help,
                    args=(skill_name, app_name, "User requested skill teaching", self.menubar.log),
                    daemon=True,
                ).start()

        self.on_submit = _do_teach

    def onPlusAction_(self, sender):
        menu = NSMenu.alloc().init()
        menu.addItem_(NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Teach New Skill (⌘T)", "onTeachAction:", "t"))
        menu.addItem_(NSMenuItem.separatorItem())
        menu.addItem_(NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(f"Toggle Voice Mode ({self._shortcut_hint()} or ⌃⌥Space)", "toggleVoiceAction:", ""))
        item_change = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Record Voice Shortcut…", "onChangeVoiceShortcutAction:", "")
        item_change.setTarget_(self)
        menu.addItem_(item_change)
        menu.addItem_(NSMenuItem.separatorItem())
        menu.addItem_(NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Clear Input (Esc)", "on_escape", ""))

        frame = sender.frame()
        loc = NSPoint(frame.origin.x, frame.origin.y + frame.size.height + 4)
        menu.popUpMenuPositioningItem_atLocation_inView_(None, loc, self.bg_view)

    def onChangeVoiceShortcutAction_(self, sender):
        if self.menubar and hasattr(self.menubar, "record_shortcut_dialog"):
            self.menubar.record_shortcut_dialog()

    def onHistoryAction_(self, sender):
        menu = NSMenu.alloc().init()
        menu.addItem_(NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Recent Tasks:", None, ""))
        tasks = [
            "Calculate (12 + 8) * 5 in Calculator and copy result",
            "Open Notes, write calculation, and share with Mr. Josh",
            "Summarize open Safari tabs",
        ]
        for t in tasks:
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(t, "onSelectHistoryTask:", "")
            item.setTarget_(self)
            item.setRepresentedObject_(t)
            menu.addItem_(item)

        frame = sender.frame()
        loc = NSPoint(frame.origin.x, frame.origin.y + frame.size.height + 4)
        menu.popUpMenuPositioningItem_atLocation_inView_(None, loc, self.bg_view)

    def onSelectHistoryTask_(self, sender):
        task = sender.representedObject()
        if task:
            self.input_field.setStringValue_(task)
            self.on_submit()

    def onModelPillAction_(self, sender):
        menu = NSMenu.alloc().init()
        current_mode = self.menubar.settings.get("mode", "auto") if self.menubar else "auto"

        modes = [
            ("Auto (Best Available)", "auto"),
            ("Hybrid (Gemini plans, Jev acts)", "hybrid"),
            ("Gemini (Planning & Vision)", "gemini"),
            ("Jev (Native & Fast)", "jev"),
        ]

        for label, m_key in modes:
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(label, "onSelectMode:", "")
            item.setTarget_(self)
            item.setRepresentedObject_(m_key)
            if m_key == current_mode:
                item.setState_(1)
            menu.addItem_(item)

        frame = sender.frame()
        loc = NSPoint(frame.origin.x, frame.origin.y + frame.size.height + 4)
        menu.popUpMenuPositioningItem_atLocation_inView_(None, loc, self.bg_view)

    def onSelectMode_(self, sender):
        chosen = sender.representedObject()
        if chosen and self.menubar:
            self.menubar.set_mode(chosen)
            self._update_model_label(chosen)

    def _update_model_label(self, mode: str):
        mode_titles = {
            "auto": "Auto ▾",
            "hybrid": "Hybrid ▾",
            "gemini": "Gemini ▾",
            "jev": "Jev ▾",
        }
        title = mode_titles.get(mode.lower(), f"{mode.capitalize()} ▾")
        if self.model_pill:
            self.model_pill.setTitle_(title)
        if self.mode_label:
            self.mode_label.setStringValue_(f"⚡ {mode.capitalize()}")

    def show(self, previous_app=None):
        self.previous_app = previous_app or NSWorkspace.sharedWorkspace().frontmostApplication()
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.panel.makeKeyAndOrderFront_(None)
        self.panel.makeFirstResponder_(self.input_field)

    def hide(self):
        if self.panel and self.panel.isVisible():
            self.panel.orderOut_(None)
        if self.previous_app is not None:
            try:
                self.previous_app.activateWithOptions_(0)
            except Exception:
                pass

    def on_escape(self):
        self.hide()

    def on_submit(self):
        text = self.input_field.stringValue().strip()
        if not text:
            return
        self.hide()
        if self.menubar:
            self.menubar.start(text)

    def onSubmitAction_(self, sender):
        self.on_submit()

    def toggleVoiceAction_(self, sender):
        if self.menubar:
            self.menubar.toggle_voice_mode()
        else:
            self.set_voice_mode_state(not self.is_voice_mode)
