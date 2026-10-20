"""Menu bar app: a status icon with tick-able options. No windows except small prompts."""

import queue
import re
import subprocess
import threading

import rumps
from AppKit import (NSApplication, NSEvent, NSEventMaskKeyDown, NSEventModifierFlagControl,
                    NSEventModifierFlagOption, NSImage, NSWorkspace)

from . import __version__, integration, say, updater, usage, voicepack
from .config import KEY_INFO, KEYS, LOG, get_key, load_keys, load_settings, save_settings, set_key

IDLE_ICON, BUSY_ICON, WARN_ICON = "cursorarrow.rays", "cursorarrow.click.2", "exclamationmark.triangle"
MIC_ICON = "mic.fill"
SPACE_KEY = 49  # ⌃⌥Space starts/stops talking


class MenuBar(rumps.App):
    def __init__(self):
        super().__init__("Computer Use", title="CU", quit_button=None)
        self.settings = load_settings()
        integration.refresh_connections()
        self.events: queue.Queue = queue.Queue()
        self.agent = None
        self.listener = None  # created on first use, so the mic is never touched unless you ask
        self.direct = False  # this utterance needs no wake word (Talk / ⌃⌥Space)
        self.voice_task = False
        self.pending = None
        self.awaiting_confirm = False

        self.status = rumps.MenuItem("Idle")
        self.new = rumps.MenuItem("New Task…", callback=self.new_task, key="n")
        self.stop_item = rumps.MenuItem("Stop", callback=None, key=".")
        self.talk = rumps.MenuItem("Talk  ⌃⌥Space", callback=lambda _: self.talk_now())

        self.usage_menu = rumps.MenuItem("Usage")
        self.modes = {
            "auto": rumps.MenuItem("Auto — best available", callback=lambda _: self.set_mode("auto")),
            "hybrid": rumps.MenuItem("Hybrid — Gemini plans, Jev acts", callback=lambda _: self.set_mode("hybrid")),
            "jev": rumps.MenuItem("Jev only — cheapest", callback=lambda _: self.set_mode("jev")),
            "gemini": rumps.MenuItem("Gemini only", callback=lambda _: self.set_mode("gemini")),
        }
        self.engine_menu = rumps.MenuItem("Listen With")
        self.voice_out_menu = rumps.MenuItem("Speaking Voice")
        self.engines: dict = {}
        self.pending_engine = None  # chosen before the Voice Pack finished installing
        mode_menu = rumps.MenuItem("Mode")
        for item in self.modes.values():
            mode_menu.add(item)

        self.toggles = {
            "hands_free": rumps.MenuItem("Hands-free — say “Computer, …”", callback=self.toggle),
            "speak_replies": rumps.MenuItem("Speak replies", callback=self.toggle),
            "narrate": rumps.MenuItem("Narrate while working", callback=self.toggle),
            "awake": rumps.MenuItem("Keep Mac awake while working", callback=self.toggle),
            "auto_switch": rumps.MenuItem("Switch models automatically when one runs out", callback=self.toggle),
            "confirm_all": rumps.MenuItem("Ask before every action", callback=self.toggle),
            "allow_risky": rumps.MenuItem("Don't ask before send / buy / delete", callback=self.toggle),
        }

        settings_menu = rumps.MenuItem("Settings")
        self.pack_item = rumps.MenuItem("Install Voice Pack…", callback=self.toggle_pack)
        self.update_item = rumps.MenuItem("Check for Updates…", callback=lambda _: self.check_updates(manual=True))
        self.auto_update_item = rumps.MenuItem("Update automatically", callback=self.toggle_auto_update)
        self.update_info = None
        self.update_ready = None
        self.login = rumps.MenuItem("Open at Login", callback=self.toggle_login)
        connect_menu = rumps.MenuItem("Connect to")
        self.clients = {name: rumps.MenuItem(name, callback=lambda _, n=name: self.toggle_client(n))
                        for name in integration.CLIENTS}
        for item in self.clients.values():
            connect_menu.add(item)
        for item in (
            rumps.MenuItem("API Keys…", callback=self.edit_keys),
            self.pack_item,
            None,
            self.login,
            connect_menu,
            None,
            rumps.MenuItem("Grant Accessibility…", callback=lambda _: integration.open_privacy_settings("Accessibility")),
            rumps.MenuItem("Grant Screen Recording…", callback=lambda _: integration.open_privacy_settings("ScreenCapture")),
            None,
            rumps.MenuItem("Open Log", callback=lambda _: _open(LOG)),
            None,
            self.update_item,
            self.auto_update_item,
            rumps.MenuItem(f"Version {__version__}", callback=None),
        ):
            settings_menu.add(item) if item is not None else settings_menu.add(rumps.separator)

        voice_menu = rumps.MenuItem("Voice")
        for item in (self.engine_menu, self.voice_out_menu, None,
                     self.toggles["hands_free"], self.toggles["speak_replies"], self.toggles["narrate"]):
            voice_menu.add(item) if item is not None else voice_menu.add(rumps.separator)
        work_toggles = [v for k, v in self.toggles.items() if k not in ("hands_free", "speak_replies", "narrate")]

        self.last = rumps.MenuItem("No tasks yet")
        self.menu = [
            self.status, self.talk, self.new, self.stop_item, None,
            self.usage_menu, mode_menu, voice_menu, None,
            *work_toggles, None,
            self.last, settings_menu, None,
            rumps.MenuItem("Quit Computer Use", callback=self.quit, key="q"),
        ]
        self.refresh()
        self.refresh_usage()
        rumps.Timer(self.pump, 0.15).start()
        rumps.Timer(lambda _: self.refresh_usage(), 30).start()
        rumps.Timer(lambda _: self.check_updates(), 6 * 3600).start()
        rumps.Timer(self._first_update_check, 8).start()
        rumps.Timer(self._first_paint, 0.05).start()
        self._install_hotkey()

    # --- state ---------------------------------------------------------------

    def _first_paint(self, timer):
        timer.stop()
        NSApplication.sharedApplication().setActivationPolicy_(1)  # menu bar only, no Dock icon
        self.set_icon(IDLE_ICON)
        if self.settings["hands_free"]:
            self.ensure_voice(self._listen)

    def set_icon(self, symbol: str):
        img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, "Computer Use")
        item = getattr(getattr(self, "_nsapp", None), "nsstatusitem", None)
        if img is not None and item is not None:
            img.setTemplate_(True)
            item.button().setImage_(img)
            item.button().setTitle_("")

    def refresh(self):
        for mode, item in self.modes.items():
            item.state = self.settings["mode"] == mode
        has_pack = voicepack.installed()
        self.build_engine_menu(has_pack)
        self.build_voice_out_menu(has_pack)
        self.pack_item.title = "Remove Voice Pack" if voicepack.HELPER.exists() else "Install Voice Pack…"
        self.pack_item.set_callback(None if (has_pack and not voicepack.HELPER.exists()) else self.toggle_pack)
        for key, item in self.toggles.items():
            item.state = self.settings[key]
        self.auto_update_item.state = self.settings["auto_update"]
        running = self.agent is not None
        self.new.set_callback(None if running else self.new_task)
        self.stop_item.set_callback(self.stop if running else None)
        bundled = integration.app_bundle() is not None
        self.login.state = integration.login_enabled()
        self.login.set_callback(self.toggle_login if bundled else None)
        for name, item in self.clients.items():
            item.state = integration.connected(name)
            item.set_callback((lambda _, n=name: self.toggle_client(n)) if integration.installed(name) else None)

    def refresh_usage(self):
        if self.usage_menu._menu is not None and len(self.usage_menu):
            self.usage_menu.clear()
        providers = [*self.settings["gemini_models"], "jev"]
        lines = usage.summary_lines(self.settings["gemini_models"])
        any_out = False
        for provider, line in zip(providers, lines):
            has_key = _env_key(provider)
            cb = (lambda _, p=provider: self.open_dashboard(p)) if has_key else (lambda _: self.edit_keys(None))
            title = line if has_key else f"{usage.pretty(provider)}: no API key (click to add)"
            item = rumps.MenuItem(title, callback=cb)
            self.usage_menu.add(item)
            any_out |= has_key and not usage.status(provider)["available"]
        self.usage_menu.add(rumps.separator)
        self.usage_menu.add(rumps.MenuItem("Click a line to open its online dashboard", callback=None))
        limits_menu = rumps.MenuItem("Set Budget / Limits")
        limits_menu.add(rumps.MenuItem("Jev Monthly Budget…", callback=lambda _: self.set_limit("jev")))
        for m in self.settings["gemini_models"]:
            limits_menu.add(rumps.MenuItem(f"{usage.pretty(m)} Daily Limit…", callback=lambda _, mod=m: self.set_limit(mod)))
        self.usage_menu.add(limits_menu)
        self.usage_menu.add(rumps.MenuItem("Clear “out of quota” flags", callback=self.clear_flags))
        if self.agent is None:
            self.set_icon(WARN_ICON if any_out else IDLE_ICON)

    def open_dashboard(self, provider: str):
        import webbrowser
        url = "https://console.typesafe.ai" if provider == "jev" else "https://aistudio.google.com"
        webbrowser.open(url)

    def set_mode(self, mode):
        self.settings["mode"] = mode
        save_settings(self.settings)
        self.refresh()

    def toggle(self, item):
        key = next(k for k, v in self.toggles.items() if v is item)
        self.settings[key] = not self.settings[key]
        save_settings(self.settings)
        self.refresh()
        if key == "hands_free":
            if self.settings["hands_free"]:
                self.ensure_voice(self._listen)
            elif self.listener and not self.direct:
                self.listener.stop()
                self.set_icon(IDLE_ICON)

    def toggle_login(self, _):
        integration.set_login(not integration.login_enabled())
        self.refresh()

    def toggle_client(self, name: str):
        was = integration.connected(name)
        integration.connect(name, not was)
        self.refresh()
        if not was:
            rumps.alert(f"Connected to {name}",
                        f"Restart {name}; its agent can then drive the Mac with the computer-use-lite tools, "
                        f"whichever model you pick there (Gemini, Claude, GPT…). {name} also needs "
                        "Accessibility and Screen Recording permission, since it starts the tools.")

    def quit(self, _):
        self._drop_listener()
        rumps.quit_application()

    # --- updates -------------------------------------------------------------

    def _first_update_check(self, timer):
        timer.stop()
        if self.settings.get("last_version") not in ("", __version__):
            rumps.notification("Computer Use", f"Updated to {__version__}", "")
        if self.settings.get("last_version") != __version__:
            self.settings["last_version"] = __version__
            save_settings(self.settings)
        self.check_updates()

    def toggle_auto_update(self, _):
        self.settings["auto_update"] = not self.settings["auto_update"]
        save_settings(self.settings)
        self.refresh()

    def check_updates(self, manual: bool = False):
        if self.update_info and manual:
            return self.start_update()

        def run():
            try:
                self.events.put(("update", (updater.check(), manual, None)))
            except Exception as e:
                self.events.put(("update", (None, manual, str(e))))

        threading.Thread(target=run, daemon=True).start()

    def on_update_checked(self, info, manual, error):
        if error:
            if manual:
                rumps.alert("Check for Updates", f"Couldn't reach GitHub: {error}")
            return
        if info is None:
            if manual:
                rumps.alert("Check for Updates", f"You're up to date (version {__version__}).")
            return
        self.update_info = info
        self.update_item.title = f"Install Update {info['version']}…"
        if self.settings["auto_update"] and updater.running_bundle():
            self.start_update()
        else:
            rumps.notification("Computer Use", f"Update {info['version']} available", "Settings › Install Update")
            if manual:
                self.start_update()

    def start_update(self):
        info = self.update_info
        if not info or getattr(self, "_updating", False):
            return
        if updater.running_bundle() is None:
            rumps.alert("Update", "Updates install into the packaged app. Running from source: use git pull.")
            return
        self._updating = True
        self.update_item.title = f"Downloading {info['version']}…"

        def run():
            try:
                dmg = updater.download(info["dmg"], lambda f: self.events.put(
                    ("update_progress", f"Downloading update… {int(f * 100)}%")))
                pack = None
                if info.get("pack") and voicepack.HELPER.exists():
                    pack = updater.download(info["pack"], lambda f: self.events.put(
                        ("update_progress", f"Downloading Voice Pack… {int(f * 100)}%")))
                self.events.put(("update_ready", (dmg, pack)))
            except Exception as e:
                self.events.put(("update_failed", str(e)))

        threading.Thread(target=run, daemon=True).start()

    def finish_update(self):
        """Install once nothing is running: swap the app, update the Voice Pack, relaunch."""
        if self.update_ready is None or self.agent is not None:
            return
        dmg, pack = self.update_ready
        self.update_ready = None
        self._drop_listener()
        try:
            if pack:
                voicepack.install(pack)
        except Exception:
            pass  # the app update matters more; the pack can be reinstalled
        rumps.notification("Computer Use", f"Installing {self.update_info['version']}", "Back in a moment.")
        updater.install_and_relaunch(dmg, updater.running_bundle())
        rumps.quit_application()

    def clear_flags(self, _):
        usage.clear_exhausted()
        self.refresh_usage()

    # --- prompts -------------------------------------------------------------

    def _prompt(self, title, message, default="", secure=False) -> str | None:
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        resp = rumps.Window(message=message, title=title, default_text=default, ok="Save",
                            cancel="Cancel", dimensions=(320, 24), secure=secure).run()
        return resp.text.strip() if resp.clicked else None

    def edit_keys(self, _):
        """One window with a field per key. Blank fields are left alone; Remove clears a saved key."""
        from AppKit import NSAlert, NSFont, NSMakeRect, NSSecureTextField, NSTextField, NSView

        rows = len(KEYS)
        width, row_h = 420, 74
        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, width, rows * row_h))
        fields = {}
        for i, name in enumerate(KEYS):
            label, hint = KEY_INFO[name]
            saved = bool(get_key(name))
            y = (rows - 1 - i) * row_h
            title = NSTextField.labelWithString_(f"{label}{'  ✓ saved' if saved else ''}")
            title.setFont_(NSFont.boldSystemFontOfSize_(12))
            title.setFrame_(NSMakeRect(0, y + 52, width, 18))
            field = NSSecureTextField.alloc().initWithFrame_(NSMakeRect(0, y + 26, width, 24))
            field.setPlaceholderString_("Saved — type a new key to replace it" if saved else "Paste key (optional)")
            note = NSTextField.labelWithString_(hint)
            note.setFont_(NSFont.systemFontOfSize_(11))
            note.setTextColor_(__import__("AppKit").NSColor.secondaryLabelColor())
            note.setFrame_(NSMakeRect(0, y + 6, width, 16))
            for v in (title, field, note):
                view.addSubview_(v)
            fields[name] = field

        alert = NSAlert.alloc().init()
        alert.setMessageText_("API Keys")
        alert.setInformativeText_("All optional. Keys are stored in your macOS Keychain.")
        alert.addButtonWithTitle_("Save")
        alert.addButtonWithTitle_("Cancel")
        alert.addButtonWithTitle_("Remove Saved Keys…")
        alert.setAccessoryView_(view)
        alert.window().setInitialFirstResponder_(fields[KEYS[0]])
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        choice = alert.runModal()
        if choice == 1000:  # Save
            for name, field in fields.items():
                value = str(field.stringValue()).strip()
                if value:
                    set_key(name, value)
        elif choice == 1002:  # Remove…
            saved = [n for n in KEYS if get_key(n)]
            for name in saved:
                if rumps.alert("Remove key", f"Remove the saved {KEY_INFO[name][0]} key?", ok="Remove",
                               cancel="Keep") == 1:
                    set_key(name, "")
        load_keys()
        self.refresh_usage()

    def set_limit(self, provider: str):
        limits = self.settings["limits"].setdefault(provider, {})
        if provider == "jev":
            v = self._prompt("Jev Budget Alert", "Monthly Jev budget in USD (blank = no limit / track only):",
                             str(limits.get("usd_per_month") or ""))
            if v is None:
                return
            limits["usd_per_month"] = float(v) if v else None
            if limits["usd_per_month"] is not None:
                d = self._prompt("Jev Billing Cycle", "Day of the month your billing resets (1–28):",
                                 str(limits.get("reset_day") or 1))
                if d:
                    try:
                        limits["reset_day"] = max(1, min(28, int(d)))
                    except ValueError:
                        pass
        else:
            v = self._prompt(usage.pretty(provider),
                             "Daily request limit for this model (blank = no limit).\n"
                             "Resets at midnight Pacific time.", str(limits.get("requests_per_day") or ""))
            if v is None:
                return
            try:
                limits["requests_per_day"] = int(v) if v else None
            except ValueError:
                pass
        save_settings(self.settings)
        self.refresh_usage()

    # --- voice ---------------------------------------------------------------

    def _install_hotkey(self):
        mods = NSEventModifierFlagControl | NSEventModifierFlagOption

        def hit(event):
            return event.keyCode() == SPACE_KEY and (event.modifierFlags() & mods) == mods

        def on_global(event):  # other apps are in front (needs Accessibility)
            if hit(event):
                self.events.put(("talk", None))

        def on_local(event):
            if hit(event):
                self.events.put(("talk", None))
                return None
            return event

        self._monitors = [NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(NSEventMaskKeyDown, on_global),
                          NSEvent.addLocalMonitorForEventsMatchingMask_handler_(NSEventMaskKeyDown, on_local)]

    def ensure_voice(self, then):
        """Create the listener for the chosen engine (asking Apple's permission if needed), then continue."""
        if self.listener is not None:
            then()
            return
        engine = self.settings["speech_engine"]
        if engine != "apple" and not voicepack.installed():
            engine = self.settings["speech_engine"] = "apple"  # pack was removed; fall back
            save_settings(self.settings)
            self.refresh()
        if engine != "apple":
            self.listener = voicepack.PackListener(engine)  # macOS asks for the microphone on first use
            self.listener._ensure()  # start the helper now so the model is loaded by the time you talk
            then()
            return
        from . import voice

        voice.authorize(lambda ok: self.events.put(("auth", (ok, then))))

    def build_engine_menu(self, has_pack: bool):
        if self.engine_menu._menu is not None and len(self.engine_menu):
            self.engine_menu.clear()
        current = voicepack.normalize(self.settings["speech_engine"])
        customs = self.settings.get("custom_speech_models", [])
        entries = [*voicepack.CATALOG.items(), *((m, f"{voicepack.label(m)} — custom") for m in customs)]
        self.engines = {}
        for model, title in entries:
            if model != "apple" and not has_pack:
                title = f"{title.split(' — ')[0]} — needs Voice Pack"
            item = rumps.MenuItem(title, callback=lambda _, m=model: self.set_engine(m))
            item.state = model == current
            self.engines[model] = item
            self.engine_menu.add(item)
        self.engine_menu.add(rumps.separator)
        self.engine_menu.add(rumps.MenuItem("Add Custom Model…", callback=self.add_custom_model))
        if customs:
            remove = rumps.MenuItem("Remove Custom Model")
            for m in customs:
                remove.add(rumps.MenuItem(voicepack.label(m), callback=lambda _, m=m: self.remove_custom_model(m)))
            self.engine_menu.add(remove)
        self.engine_menu.add(rumps.MenuItem("How to Add Models…", callback=lambda _: rumps.alert(
            "Adding speech models", voicepack.ADD_MODEL_HELP)))

    def build_voice_out_menu(self, has_pack: bool):
        """Speaking Voice ▸ Mac ▸ / Kokoro ▸ / Gemini ▸, each voice previews itself when picked."""
        if self.voice_out_menu._menu is not None and len(self.voice_out_menu):
            self.voice_out_menu.clear()
        current = self.settings.get("speaking_voice") or "mac:"
        has_gemini = bool(_env_key("gemini"))
        groups = [
            ("Mac", [("mac:", "System voice")] + [(f"mac:{n}", f"{n} — {loc.replace('_', '-')}")
                                                   for n, loc in say.mac_voices()[:16]], True),
            ("Kokoro — local, natural" if has_pack else "Kokoro — needs Voice Pack",
             [(f"kokoro:{k}", v) for k, v in voicepack.KOKORO_VOICES.items()], has_pack),
            ("Gemini — cloud" if has_gemini else "Gemini — needs API key",
             [(f"gemini:{k}", f"{k} — {v}") for k, v in say.GEMINI_VOICES.items()], has_gemini),
        ]
        for title, voices, enabled in groups:
            group = rumps.MenuItem(title)
            for vid, label in voices:
                item = rumps.MenuItem(label, callback=(lambda _, v=vid: self.set_voice(v)) if enabled else None)
                item.state = vid == current
                group.add(item)
            group.state = current.split(":")[0] == voices[0][0].split(":")[0]
            self.voice_out_menu.add(group)
        self.voice_out_menu.add(rumps.separator)
        self.voice_out_menu.add(rumps.MenuItem("Get More Mac Voices…", callback=lambda _: subprocess.run(
            ["open", "x-apple.systempreferences:com.apple.preference.universalaccess?SpokenContent"])))

    def set_voice(self, voice_id: str):
        self.settings["speaking_voice"] = voice_id
        save_settings(self.settings)
        self.refresh()
        say.speak("Hi, this is how I'll sound while I work.", voice_id)  # preview

    def add_custom_model(self, _):
        text = self._prompt("Add Custom Model", voicepack.ADD_MODEL_HELP, default="")
        if not text:
            return
        model = voicepack.guess_model(text)
        if model is None:
            rumps.alert("Add Custom Model", f"Couldn't tell what kind of model “{text}” is.\n\n"
                        "Only Parakeet (MLX) and Moonshine (ONNX) models are supported. If it is one, "
                        "add parakeet: or moonshine: in front of the name.")
            return
        customs = self.settings.setdefault("custom_speech_models", [])
        if model not in customs and model not in voicepack.CATALOG:
            customs.append(model)
            save_settings(self.settings)
        self.set_engine(model)

    def remove_custom_model(self, model: str):
        self.settings["custom_speech_models"] = [m for m in self.settings.get("custom_speech_models", []) if m != model]
        if voicepack.normalize(self.settings["speech_engine"]) == model:
            self.settings["speech_engine"] = "apple"
            self._drop_listener()
        save_settings(self.settings)
        self.refresh()

    def set_engine(self, engine: str):
        if engine != "apple" and not voicepack.installed():
            self.pending_engine = engine
            self.toggle_pack(None)
            return
        self.settings["speech_engine"] = engine
        save_settings(self.settings)
        self._drop_listener()
        self.refresh()
        if self.settings["hands_free"]:
            self.ensure_voice(self._listen)

    def talk_now(self):
        """Talk / ⌃⌥Space: start listening without a wake word, or end the utterance if already listening."""
        if self.listener and self.listener.listening and self.direct:
            self.listener._finish()
            return
        self.direct = True
        if self.listener and self.listener.listening:
            return  # hands-free session already open; this utterance just skips the wake word
        self.ensure_voice(self._listen)

    def _listen(self):
        if self.listener is None or self.listener.listening or self.awaiting_confirm:
            return
        self.listener.start()
        if self.listener.listening:
            self.set_icon(MIC_ICON)
            if self.direct and self.agent is None:
                self.status.title = "Listening…"

    def on_speech(self, kind, text):
        from . import voice

        if kind == "status":
            self.status.title = _short(text, 60)
            return
        if kind == "error":
            self.direct = False
            self.status.title = f"Voice: {_short(text, 50)}"
            return
        if say.is_echo(text):
            return  # our own narration coming back through the mic
        if kind == "partial":
            if self.direct or voice.strip_wake_word(text) is not None or (self.agent and voice.is_stop(text)):
                self.status.title = f"🎙 {_short(text, 56)}"
            return
        # final utterance
        direct, self.direct = self.direct, False
        self.set_icon(BUSY_ICON if self.agent else IDLE_ICON)
        command = voice.strip_wake_word(text)
        if command is None and (direct or (self.agent and voice.is_stop(text))):
            command = text.strip()
        if not command:
            if self.agent is None:
                self.status.title = "Idle"
            return
        if voice.is_stop(command):
            if self.agent:
                self.stop(None)
                self.say("Stopping.")
            return
        self.log(f"🎙 {command}")
        if self.agent:
            # A new instruction mid-task replaces the current one.
            self.pending = command
            self.stop(None)
            self.say("Okay, switching.")
        else:
            self.voice_task = True
            self.say("On it.")
            self.start(command)

    def _drop_listener(self):
        if self.listener is not None:
            self.listener.stop()
            if hasattr(self.listener, "close"):
                self.listener.close()
            self.listener = None

    def toggle_pack(self, _):
        if voicepack.HELPER.exists():
            if rumps.alert("Remove Voice Pack?", "Parakeet and Moonshine will no longer be available. "
                           "Downloaded models stay in ~/.cache/huggingface.", ok="Remove", cancel="Cancel") == 1:
                self._drop_listener()
                voicepack.uninstall()
                if self.settings["speech_engine"] != "apple":
                    self.set_engine("apple")
                self.refresh()
            return
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        zip_path = voicepack.find_pack()
        if zip_path is None or rumps.alert(
                "Install Voice Pack", f"Install from “{zip_path.name}” in {zip_path.parent}?",
                ok="Install", cancel="Choose Another…") != 1:
            zip_path = _choose_zip()
        if zip_path is None:
            return
        self.status.title = "Installing Voice Pack…"
        threading.Thread(target=self._install_pack, args=(zip_path,), daemon=True).start()

    def _install_pack(self, zip_path):
        try:
            voicepack.install(zip_path)
            self.events.put(("pack", None))
        except Exception as e:
            self.events.put(("pack", str(e)))

    def say(self, text: str):
        if self.settings.get("speak_replies"):
            say.speak(text, self.settings.get("speaking_voice"))

    def narrate(self, text: str):
        """From the worker thread: a short spoken update, dropped if something is still being said."""
        if text and self.settings.get("narrate"):
            self.events.put(("narrate", text))

    def _voice_tick(self):
        if self.listener is None:
            return
        while not self.listener.events.empty():
            self.on_speech(*self.listener.events.get())
        self.listener.tick()
        if self.settings["hands_free"] and not self.listener.listening and not self.awaiting_confirm:
            if not say.speaking():  # don't hear ourselves
                self._listen()

    # --- running -------------------------------------------------------------

    def new_task(self, _):
        previous = NSWorkspace.sharedWorkspace().frontmostApplication()
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        resp = rumps.Window(
            message="What should I do? I'll work in whichever app is in front.",
            title="New Task", default_text="", ok="Run", cancel="Cancel", dimensions=(360, 72),
        ).run()
        task = resp.text.strip()
        if previous is not None:
            previous.activateWithOptions_(0)  # hand focus back to the app being driven
        if resp.clicked and task:
            self.start(task)

    def start(self, task: str):
        from .agent import Agent

        self.settings = load_settings()
        try:
            self.agent = Agent(
                mode=self.settings["mode"], confirm_all=self.settings["confirm_all"],
                allow_risky=self.settings["allow_risky"], auto_switch=self.settings["auto_switch"],
                confirm=self.confirm, log=self.log, narrate=self.narrate,
            )
        except RuntimeError as e:
            rumps.alert("Computer Use", str(e))
            return
        self.status.title = f"Working: {_short(task)}"
        self.set_icon(BUSY_ICON)
        self.refresh()
        self.log(f"\n▶ {task}  [{self.agent.mode}]")
        threading.Thread(target=self.work, args=(self.agent, task), daemon=True).start()

    def work(self, agent, task):
        from .awake import stay_awake

        try:
            with stay_awake(self.settings["awake"]):
                result = agent.run(task)
        except Exception as e:
            result = f"error: {e}"
            self.log(result)
        self.events.put(("finished", result.splitlines()[0]))

    def stop(self, _):
        if self.agent:
            self.agent.stop.set()
            self.status.title = "Stopping…"

    def log(self, line: str):
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as f:
            f.write(line + "\n")
        self.events.put(("status", line.strip()))

    def confirm(self, msg: str) -> bool:
        # Called from the worker thread; the alert has to run on the main thread.
        done, answer = threading.Event(), []
        self.events.put(("confirm", (msg, done, answer)))
        done.wait()
        return answer[0]

    def pump(self, _):
        self._voice_tick()
        while not self.events.empty():
            kind, payload = self.events.get()
            if kind == "talk":
                self.talk_now()
                continue
            if kind == "update":
                self.on_update_checked(*payload)
                continue
            if kind == "update_progress":
                self.update_item.title = payload
                if self.agent is None:
                    self.status.title = payload
                continue
            if kind == "update_failed":
                self._updating = False
                self.update_item.title = f"Install Update {self.update_info['version']}…"
                self.status.title = "Idle" if self.agent is None else self.status.title
                rumps.notification("Computer Use", "Update failed", payload)
                continue
            if kind == "update_ready":
                self.update_ready = payload
                self.update_item.title = "Installing when idle…"
                self.finish_update()
                continue
            if kind == "narrate":
                say.speak(payload, self.settings.get("speaking_voice"), interrupt=False)
                continue
            if kind == "pack":
                self.status.title = "Idle" if self.agent is None else self.status.title
                self.refresh()
                if payload:
                    rumps.alert("Voice Pack", f"Couldn't install: {payload}")
                else:
                    rumps.notification("Computer Use", "Voice Pack installed",
                                       "The model downloads the first time you talk.")
                    if self.pending_engine:
                        engine, self.pending_engine = self.pending_engine, None
                        self.set_engine(engine)
                continue
            if kind == "auth":
                ok, then = payload
                if ok:
                    from .voice import Listener
                    self.listener = self.listener or Listener()
                    then()
                else:
                    self.direct = False
                    rumps.alert("Computer Use", "Speech recognition isn't allowed. Turn it on in System Settings › "
                                "Privacy & Security › Speech Recognition, and allow the microphone.")
                continue
            if kind == "status" and self.agent and not self.agent.stop.is_set():
                self.status.title = _short(payload, 60)
            elif kind == "confirm":
                msg, done, answer = payload
                self.awaiting_confirm = True
                if self.listener:
                    self.listener.stop()
                self.say(msg.replace("About to:", "Should I").rstrip(". ") + "?")
                NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
                answer.append(rumps.alert("Computer Use", msg, ok="Allow", cancel="Stop") == 1)
                self.awaiting_confirm = False
                done.set()
            elif kind == "finished":
                self.agent = None
                self.status.title = "Idle"
                self.last.title = f"Last: {_short(payload, 50)}"
                self.refresh()
                self.refresh_usage()
                if self.pending:
                    task, self.pending = self.pending, None
                    self.start(task)
                    continue
                if self.update_ready:
                    self.finish_update()
                if self.voice_task:
                    self.say(_spoken(payload))
                    self.voice_task = False
                rumps.notification("Computer Use", "Task finished", payload)


def _env_key(provider: str) -> bool:
    import os
    names = ("TYPESAFE_API_KEY", "JEV_API_KEY") if provider == "jev" else ("GEMINI_API_KEY", "GOOGLE_API_KEY")
    return any(os.environ.get(n) for n in names)


def _choose_zip():
    from AppKit import NSOpenPanel
    panel = NSOpenPanel.openPanel()
    panel.setTitle_("Choose the Computer Use Voice Pack (.zip)")
    panel.setAllowedFileTypes_(["zip"])
    if panel.runModal() == 1:
        from pathlib import Path
        return Path(str(panel.URLs()[0].path()))
    return None


def _spoken(result: str) -> str:
    """'done (jev p=0.91)' -> 'Done.'"""
    text = re.sub(r"\s*\([^)]*\)", "", result).strip(" .")
    return (text[:1].upper() + text[1:] + ".") if text else "Done."


def _short(s: str, n: int = 40) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _open(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    NSWorkspace.sharedWorkspace().openFile_(str(path))


def main():
    load_keys()
    MenuBar().run()


if __name__ == "__main__":
    main()
