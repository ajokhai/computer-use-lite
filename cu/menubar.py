"""Menu bar app: a status icon with tick-able options. No windows except small prompts."""

import os
import queue
from collections import deque
import subprocess
import threading
import time

import rumps
from AppKit import (
    NSApplication,
    NSColor,
    NSEvent,
    NSEventMaskFlagsChanged,
    NSEventMaskKeyDown,
    NSImage,
    NSWorkspace,
)

from . import __version__, integration, onboarding, replies, say, updater, usage, voicepack
from .config import DATA, LOG, load_keys, load_settings, save_settings
from .shortcut import PRESETS, Detector, Shortcut
from .logger import CRASH_LOG, rotate, setup_logging

setup_logging()

# The menu bar icon shows what's happening: idle, working (orange and pulsing: this app's agent, or another
# AI driving the Mac through cu-mcp) or listening (red). cu-mcp logs every call, so a fresh mcp.log means busy.
ICONS = {"idle": ("cursorarrow.rays", None, "Idle"),
         "working": ("cursorarrow.click.2", "systemOrangeColor", "An agent is using your Mac"),
         "listening": ("mic.fill", "systemRedColor", "Listening")}
MCP_LOG = LOG.parent / "mcp.log"
MCP_BUSY_SECS = 6

# Pressing Escape three times stops any agent from anywhere. Escapes an agent types itself don't count.
ESCAPE, ESCAPES_TO_STOP, ESCAPE_WINDOW = 53, 3, 1.5
STOP_REQUEST = DATA / "stop-request"  # its time tells cu-mcp to refuse actions for a moment


class MenuBar(rumps.App):
    def __init__(self):
        super().__init__("Computer Use", title="CU", quit_button=None)
        self.settings = load_settings()
        integration.refresh_connections()
        self.events: queue.Queue = queue.Queue()
        self.agent = None
        self.listener = None  # created on first use, so the mic is never touched unless you ask
        self.direct = False  # this utterance needs no wake word (the shortcut or the mic button)
        self.voice_task = False
        self.pending = None
        self.awaiting_confirm = False
        self.prompt_bar = None
        self.last_task, self.last_log = "", deque(maxlen=80)  # for Skills › Teach › From the Last Task

        self.status = rumps.MenuItem("Idle")
        self.last = rumps.MenuItem("")
        self.last._menuitem.setHidden_(True)  # shown once a task has finished
        self.stop_item = rumps.MenuItem("Stop  (Esc Esc Esc)", callback=None, key=".")
        self.ask = rumps.MenuItem("Ask Computer Use…", callback=lambda _: self.open_bar())
        self.setup_item = rumps.MenuItem("Finish Setting Up…", callback=lambda _: self.show_setup())

        self.modes = {
            "auto": rumps.MenuItem("Auto: Best Available", callback=lambda _: self.set_mode("auto")),
            "hybrid": rumps.MenuItem("Vision + Jev: Vision Model Plans, Jev Acts", callback=lambda _: self.set_mode("hybrid")),
            "jev": rumps.MenuItem("Jev Only: Cheapest", callback=lambda _: self.set_mode("jev")),
            "vision": rumps.MenuItem("Vision Only: Vision Model Does Everything", callback=lambda _: self.set_mode("vision")),
        }
        mode_menu = rumps.MenuItem("Mode")
        for item in self.modes.values():
            mode_menu.add(item)
        self.vision_menu = rumps.MenuItem("Vision Model")
        mode_menu.add(rumps.separator)
        mode_menu.add(self.vision_menu)

        self.toggles = {
            "open_listening": rumps.MenuItem("Start Listening When the Bar Opens", callback=self.toggle),
            "hands_free": rumps.MenuItem("Hands-free: Say “Computer, …”", callback=self.toggle),
            "speak_replies": rumps.MenuItem("Speak Replies", callback=self.toggle),
            "narrate": rumps.MenuItem("Narrate While Working", callback=self.toggle),
            "confirm_all": rumps.MenuItem("Ask Before Every Action", callback=self.toggle),
            "allow_risky": rumps.MenuItem("Don't Ask Before Paying or Deleting", callback=self.toggle),
            "awake": rumps.MenuItem("Keep Mac Awake", callback=self.toggle),
            "auto_switch": rumps.MenuItem("Switch Models When One Runs Out", callback=self.toggle),
        }

        self.engine_menu = rumps.MenuItem("Listen With")
        self.voice_out_menu = rumps.MenuItem("Speaking Voice")
        self.engines: dict = {}
        self.pending_engine = None  # chosen before the Voice Pack finished installing
        self.pack_item = rumps.MenuItem("Install Voice Pack…", callback=self.toggle_pack)
        voice_menu = rumps.MenuItem("Voice")
        t = self.toggles
        _fill(voice_menu, [self.engine_menu, self.voice_out_menu, None,
                           t["open_listening"], t["hands_free"], t["speak_replies"], t["narrate"], None, self.pack_item])

        self.usage_menu = rumps.MenuItem("Usage")
        self.skills_menu = rumps.MenuItem("Skills")
        working_menu = rumps.MenuItem("While Working")
        _fill(working_menu, [t["confirm_all"], t["allow_risky"], None, t["awake"], t["auto_switch"]])

        connect_menu = rumps.MenuItem("Connect AI Apps")
        self.clients = {name: rumps.MenuItem(name, callback=lambda _, n=name: self.toggle_client(n))
                        for name in integration.CLIENTS}
        _fill(connect_menu, list(self.clients.values()))

        self.shortcut_menu = rumps.MenuItem("Shortcut")
        self.update_item = rumps.MenuItem("Check for Updates…", callback=lambda _: self.check_updates(manual=True))
        self.auto_update_item = rumps.MenuItem("Update Automatically", callback=self.toggle_auto_update)
        self.update_info = None
        self.update_ready = None
        self.login = rumps.MenuItem("Open at Login", callback=self.toggle_login)
        settings_menu = rumps.MenuItem("Settings")
        _fill(settings_menu, [
            rumps.MenuItem("API Keys…", callback=self.edit_keys),
            self.shortcut_menu,
            self.login,
            None,
            rumps.MenuItem("Set Up Permissions…", callback=lambda _: self.show_setup()),
            None,
            self.update_item,
            self.auto_update_item,
            None,
            rumps.MenuItem("Open Log", callback=lambda _: _open(LOG)),
            rumps.MenuItem("Open Logs Folder", callback=lambda _: _open(CRASH_LOG.parent)),
            None,
            rumps.MenuItem(f"Version {__version__}", callback=None),
        ])

        self.menu = [
            self.setup_item, self.status, self.last, self.stop_item, None,
            self.ask, None,
            mode_menu, voice_menu, self.usage_menu, self.skills_menu, None,
            working_menu, connect_menu, settings_menu, None,
            rumps.MenuItem("Quit Computer Use", callback=self.quit, key="q"),
        ]
        usage.clear_exhausted()
        self.refresh()
        self.refresh_usage()
        rumps.Timer(self.pump, 0.1).start()
        rumps.Timer(lambda _: self.refresh_usage(), 30).start()
        rumps.Timer(lambda _: self.check_updates(), 6 * 3600).start()
        rumps.Timer(self._first_update_check, 8).start()
        rumps.Timer(self._first_paint, 0.05).start()

    # --- state ---------------------------------------------------------------

    def _first_paint(self, timer):
        timer.stop()
        NSApplication.sharedApplication().setActivationPolicy_(1)  # menu bar only, no Dock icon
        self._icon = None  # pump() paints it
        self._install_hotkey()
        if not self.settings.get("onboarded") or onboarding.missing_required():
            self.show_setup()
        if self.settings["hands_free"]:
            self.ensure_voice(self._listen)

    def set_icon(self, state: str):
        symbol, color, tip = ICONS[state]
        img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, f"Computer Use: {tip}")
        item = getattr(getattr(self, "_nsapp", None), "nsstatusitem", None)
        if img is not None and item is not None:
            img.setTemplate_(True)
            button = item.button()
            button.setImage_(img)
            button.setTitle_("")
            button.setContentTintColor_(getattr(NSColor, color)() if color else None)
            button.setToolTip_(f"Computer Use: {tip}")
            button.setAlphaValue_(1.0)

    def icon_state(self) -> str:
        if self.agent is not None or self._mcp_busy():
            return "working"
        if self.bar_listening() or (self.direct and self.listener is not None and self.listener.listening):
            return "listening"
        return "idle"

    def _mcp_busy(self) -> bool:
        now = time.monotonic()
        if now - getattr(self, "_mcp_checked", 0.0) > 0.5:  # one stat every half second
            self._mcp_checked = now
            try:
                self._mcp_recent = time.time() - MCP_LOG.stat().st_mtime < MCP_BUSY_SECS
            except OSError:
                self._mcp_recent = False
        return self._mcp_recent

    def _icon_tick(self):
        state = self.icon_state()
        if state != getattr(self, "_icon", None):
            self._icon = state
            self.set_icon(state)
        if state == "working":  # a slow pulse, so it reads as activity even in a glance
            item = getattr(getattr(self, "_nsapp", None), "nsstatusitem", None)
            if item is not None:
                item.button().setAlphaValue_(1.0 if int(time.monotonic() * 1.5) % 2 == 0 else 0.45)

    def refresh(self):
        for mode, item in self.modes.items():
            item.state = self.settings["mode"] == mode
        has_pack = voicepack.installed()
        self.build_engine_menu(has_pack)
        self.build_voice_out_menu(has_pack)
        self.build_shortcut_menu()
        self.build_skills_menu()
        self.build_vision_menu()
        self.pack_item.title = "Remove Voice Pack…" if voicepack.HELPER.exists() else "Install Voice Pack…"
        self.pack_item.set_callback(None if (has_pack and not voicepack.HELPER.exists()) else self.toggle_pack)
        for key, item in self.toggles.items():
            item.state = self.settings[key]
        self.auto_update_item.state = self.settings["auto_update"]
        self.ask.title = f"Ask Computer Use…   {self.shortcut().label}"
        self.setup_item._menuitem.setHidden_(not onboarding.missing_required())
        self.stop_item.set_callback(self.stop if self.agent is not None else None)
        bundled = integration.app_bundle() is not None
        self.login.state = integration.login_enabled()
        self.login.set_callback(self.toggle_login if bundled else None)
        for name, item in self.clients.items():
            item.state = integration.connected(name)
            item.set_callback((lambda _, n=name: self.toggle_client(n)) if integration.installed(name) else None)

    def refresh_usage(self):
        """One line per provider in use; its submenu has each model's usage, the dashboard and limits."""
        if self.usage_menu._menu is not None and len(self.usage_menu):
            self.usage_menu.clear()
        from . import vision
        groups = [(p, vision.models(p, self.settings)) for p in vision.PROVIDERS if vision.has_key(p)]
        if _env_key("jev"):
            groups.insert(0, ("jev", ["jev"]))
        if not groups:
            self.usage_menu.add(rumps.MenuItem("No Models Set Up Yet", callback=None))
        for p, ms in groups:
            states = [usage.status(m) for m in ms]
            if p == "jev":
                head = f"Jev: ${states[0]['used']:.2f} this month"
                lines = usage.summary_lines([])
            else:
                head = f"{vision.LABELS[p]}: {sum(st['requests'] for st in states)} requests today"
                lines = usage.summary_lines(ms)[:len(ms)]
            if not any(st["available"] for st in states):
                head += " · out of quota"
            sub = rumps.MenuItem(head)
            for line in lines:
                sub.add(rumps.MenuItem(line, callback=None))
            sub.add(rumps.separator)
            if p != "local":
                sub.add(rumps.MenuItem("Open Dashboard", callback=lambda _, p=p: self.open_dashboard(p)))
            if p == "jev":
                sub.add(rumps.MenuItem("Set Monthly Budget…", callback=lambda _: self.set_limit("jev")))
            else:
                for m in ms:
                    sub.add(rumps.MenuItem(f"Set {usage.pretty(m)} Daily Limit…", callback=lambda _, m=m: self.set_limit(m)))
            self.usage_menu.add(sub)
        self.usage_menu.add(rumps.separator)
        self.usage_menu.add(rumps.MenuItem("Add a Model…", callback=self.edit_keys))
        self.usage_menu.add(rumps.MenuItem("Clear Out-of-Quota Flags", callback=self.clear_flags))

    def open_dashboard(self, provider: str):
        import webbrowser
        url = {"jev": "https://console.typesafe.ai", "gemini": "https://aistudio.google.com",
               "claude": "https://platform.claude.com", "openai": "https://platform.openai.com/usage",
               "openrouter": "https://openrouter.ai/activity"}.get(provider)
        if url:
            webbrowser.open(url)

    def set_mode(self, mode):
        self.settings["mode"] = mode
        save_settings(self.settings)
        self.refresh()
        if self.prompt_bar is not None:
            self.prompt_bar.set_mode(mode)

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
        """Models and Keys: each provider's status, with a button to add or change its key."""
        from . import keys_window
        keys_window.show(self)

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
                             "Counted by this app; resets at midnight Pacific time.",
                             str(limits.get("requests_per_day") or ""))
            if v is None:
                return
            try:
                limits["requests_per_day"] = int(v) if v else None
            except ValueError:
                pass
        save_settings(self.settings)
        self.refresh_usage()

    def show_setup(self):
        onboarding.show(self)

    # --- the vision model -------------------------------------------------------

    def build_vision_menu(self):
        """Mode › Vision Model: Auto, the models that are set up, and Add a Model…."""
        from . import vision
        if self.vision_menu._menu is not None and len(self.vision_menu):
            self.vision_menu.clear()
        choice = self.settings.get("vision_model") or "auto"
        auto = rumps.MenuItem("Auto", callback=lambda _: self.set_vision_model("auto"))
        auto.state = choice == "auto"
        self.vision_menu.add(auto)
        ready = [m for p in vision.PROVIDERS if vision.has_key(p) for m in vision.models(p, self.settings)]
        if ready:
            self.vision_menu.add(rumps.separator)
        for m in ready:
            item = rumps.MenuItem(usage.pretty(m), callback=lambda _, m=m: self.set_vision_model(m))
            item.state = choice == m
            self.vision_menu.add(item)
        self.vision_menu.add(rumps.separator)
        self.vision_menu.add(rumps.MenuItem("Add a Model…", callback=self.edit_keys))

    def set_vision_model(self, model: str):
        self.settings["vision_model"] = model
        save_settings(self.settings)
        self.refresh()

    # --- skills ----------------------------------------------------------------

    def build_skills_menu(self):
        """Skills grouped by app or site, then ways to teach, import and export them."""
        from . import skills
        if self.skills_menu._menu is not None and len(self.skills_menu):
            self.skills_menu.clear()
        groups = skills.categories()
        for place in sorted(groups):
            self.skills_menu.add(rumps.MenuItem(place, callback=None))
            for sk in groups[place]:
                wins = sk.get("wins", 0)
                title = f"    {sk['name']}" + (f"  ·  {wins} win{'s' if wins != 1 else ''}" if wins else "")
                self.skills_menu.add(rumps.MenuItem(title, callback=lambda _, sk=sk: self.show_skill(sk)))
        if not groups:
            self.skills_menu.add(rumps.MenuItem("No Skills Yet", callback=None))
        teach = rumps.MenuItem("Teach a Skill")
        _fill(teach, [
            rumps.MenuItem("From the Last Task…", callback=lambda _: self.teach_from_last() if self.last_task else None),
            rumps.MenuItem("Write It Myself…", callback=lambda _: self.teach_by_writing()),
            rumps.MenuItem("From a Screen Recording…", callback=lambda _: self.teach_from_video()),
        ])
        teach["From the Last Task…"].set_callback((lambda _: self.teach_from_last()) if self.last_task else None)
        _fill(self.skills_menu, [None, teach,
                                 rumps.MenuItem("Import Skills…", callback=lambda _: self.import_file("skills")),
                                 rumps.MenuItem("Export Skills…", callback=lambda _: self.export_file("skills")),
                                 None,
                                 rumps.MenuItem("Import Lessons…", callback=lambda _: self.import_file("lessons")),
                                 rumps.MenuItem("Export Lessons…", callback=lambda _: self.export_file("lessons"))])

    def show_skill(self, sk: dict):
        from . import skills
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        choice = rumps.alert(sk["name"], skills.as_text(sk), ok="Close", cancel="Delete", other="Export…")
        if choice == 0 and rumps.alert("Delete this skill?", sk["name"], ok="Delete", cancel="Keep") == 1:
            skills.delete(sk["id"])
            self.build_skills_menu()
        elif choice == -1:
            self.export_file("skills", ids=[sk["id"]], name=f"{sk['name']}.json")

    def _vision(self):
        """The vision model for distilling skills, or None if no model is set up."""
        from . import vision
        return vision.Vision(log=self.log) if vision.configured() else None

    def _learn_in_background(self, make):
        """Run a skill maker off the main thread; its result (or error) comes back as an alert."""
        def run():
            try:
                sk = make()
                self.events.put(("skill", f"Saved “{sk['name']}”.\n\n" + __import__("cu.skills").skills.as_text(sk)))
            except Exception as e:
                self.events.put(("skill", f"Couldn't make a skill: {e}"))
        threading.Thread(target=run, daemon=True).start()

    def teach_from_last(self):
        from . import skills
        name = self._prompt("Teach a Skill", f"Keep how this was done as a skill:\n“{_short(self.last_task, 80)}”\n\nName it:")
        if name is None:
            return
        v, task, log = self._vision(), self.last_task, list(self.last_log)
        self.status.title = "Making a skill…"
        self._learn_in_background(lambda: skills.from_run(task, log, v.ask_json if v else (lambda s, t: None), name))

    def teach_by_writing(self):
        from AppKit import NSAlert, NSMakeRect, NSScrollView, NSTextField, NSTextView, NSView
        from . import skills
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Write a Skill")
        alert.setInformativeText_("One step per line, as you'd tell someone new to the app.")
        alert.addButtonWithTitle_("Save")
        alert.addButtonWithTitle_("Cancel")
        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 340, 200))
        name = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 174, 340, 24))
        name.setPlaceholderString_("Name, e.g. Post on X")
        place = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 144, 340, 24))
        place.setPlaceholderString_("App or site, e.g. x.com")
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, 340, 136))
        scroll.setBorderType_(2)
        scroll.setHasVerticalScroller_(True)
        steps = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 336, 136))
        steps.setFont_(name.font())
        scroll.setDocumentView_(steps)
        for v in (name, place, scroll):
            view.addSubview_(v)
        alert.setAccessoryView_(view)
        alert.window().setInitialFirstResponder_(name)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        if alert.runModal() != 1000:  # the first button, Save
            return
        lines = [ln.strip() for ln in str(steps.string()).splitlines() if ln.strip()]
        try:
            sk = skills.save({"name": str(name.stringValue()), "where": str(place.stringValue()), "steps": lines}, "user")
            self.build_skills_menu()
            rumps.notification("Computer Use", "Skill saved", sk["name"])
        except ValueError as e:
            rumps.alert("Write a Skill", str(e).capitalize())

    def teach_from_video(self):
        from AppKit import NSOpenPanel
        from . import skills
        from .vision.base import parse_json
        v = self._vision()
        if v is None:
            rumps.alert("Teach a Skill", "Add a vision model key to learn from videos (Settings › API Keys…).")
            return
        panel = NSOpenPanel.openPanel()
        panel.setAllowedFileTypes_(["mov", "mp4", "m4v"])
        panel.setMessage_("Choose a screen recording of the task being done (up to 500 MB).")
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        if panel.runModal() != 1:
            return
        path = str(panel.URL().path())
        if __import__("os").path.getsize(path) > 500 * 1024 * 1024:
            rumps.alert("Teach a Skill", "That recording is over 500 MB. Trim it to just the task and try again.")
            return
        name = self._prompt("Teach a Skill", "Name the skill this recording shows:") or ""
        self.status.title = "Watching the recording…"
        ask = lambda system, text, png: parse_json(v.current()._json(system, text, png))
        self._learn_in_background(lambda: skills.from_video(path, ask, name))

    def import_file(self, kind: str):
        from AppKit import NSOpenPanel
        from . import memory, skills
        panel = NSOpenPanel.openPanel()
        panel.setAllowedFileTypes_(["json"])
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        if panel.runModal() != 1:
            return
        try:
            n = (skills.import_ if kind == "skills" else memory.import_)(str(panel.URL().path()))
            self.build_skills_menu()
            rumps.notification("Computer Use", f"Imported {n} {kind}", "")
        except (ValueError, OSError) as e:
            rumps.alert(f"Import {kind.capitalize()}", f"Couldn't import that file: {e}")

    def export_file(self, kind: str, ids: list[str] | None = None, name: str = ""):
        from AppKit import NSSavePanel
        from . import memory, skills
        panel = NSSavePanel.savePanel()
        panel.setNameFieldStringValue_(name or f"Computer Use {kind.capitalize()}.json")
        panel.setMessage_("Personal details (keys, emails, handles, names, links' paths) are removed.")
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        if panel.runModal() != 1:
            return
        path = str(panel.URL().path())
        n = skills.export(path, ids) if kind == "skills" else memory.export(path)
        rumps.notification("Computer Use", f"Exported {n} {kind}", path)

    # --- the shortcut ----------------------------------------------------------

    def shortcut(self) -> Shortcut:
        return Shortcut.from_setting(self.settings.get("shortcut") or self.settings.get("voice_shortcut"))

    def build_shortcut_menu(self):
        if self.shortcut_menu._menu is not None and len(self.shortcut_menu):
            self.shortcut_menu.clear()
        current = self.shortcut()
        for s in (PRESETS if current in PRESETS else [*PRESETS, current]):
            item = rumps.MenuItem(s.title, callback=lambda _, s=s: self.set_shortcut(s))
            item.state = s == current
            self.shortcut_menu.add(item)
        self.shortcut_menu.add(rumps.separator)
        self.shortcut_menu.add(rumps.MenuItem("Record Shortcut…", callback=lambda _: self.record_shortcut()))

    def record_shortcut(self):
        from .shortcut_recorder import show_shortcut_recorder
        show_shortcut_recorder(self.shortcut(), self.set_shortcut)

    def set_shortcut(self, s: Shortcut):
        self.settings["shortcut"] = s.to_setting()
        self.settings.pop("voice_shortcut", None)
        save_settings(self.settings)
        self.refresh()
        self._install_hotkey()

    def _install_hotkey(self):
        """Key shortcuts go through Carbon (swallowed system-wide); double-taps are spotted
        by watching modifier changes, and any typing in between cancels them."""
        from . import hotkey
        for m in getattr(self, "_monitors", []):
            NSEvent.removeMonitor_(m)
        hotkey.unregister()
        current = self.shortcut()
        detector = self._detector = Detector(current)
        if current.kind == "key" and not hotkey.register(current.key_code, current.mods, self.shortcut_pressed):
            rumps.notification("Computer Use", f"{current.label} is taken",
                               "Another app uses that shortcut. Pick another in Settings › Shortcut.")

        def on_key(event):
            if event.keyCode() == ESCAPE:
                self.on_escape(event)
            detector.key_down(event.keyCode(), event.modifierFlags(), time.monotonic())

        def on_flags(event):
            self.on_gesture(detector.flags_changed(event.modifierFlags(), time.monotonic()))

        def local(handler):
            def run(event):
                handler(event)
                return event
            return run

        self._monitors = [
            NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(NSEventMaskKeyDown, on_key),
            NSEvent.addLocalMonitorForEventsMatchingMask_handler_(NSEventMaskKeyDown, local(on_key)),
            NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(NSEventMaskFlagsChanged, on_flags),
            NSEvent.addLocalMonitorForEventsMatchingMask_handler_(NSEventMaskFlagsChanged, local(on_flags)),
        ]

    def on_escape(self, event):
        if _typed_by_an_agent(event):
            return
        now = time.monotonic()
        self._escapes = [t for t in getattr(self, "_escapes", []) if now - t < ESCAPE_WINDOW] + [now]
        if len(self._escapes) >= ESCAPES_TO_STOP:
            self._escapes = []
            self.stop_everything()

    def stop_everything(self):
        """Stop this app's agent and tell cu-mcp to stop acting for whichever AI is driving it."""
        try:
            STOP_REQUEST.parent.mkdir(parents=True, exist_ok=True)
            STOP_REQUEST.write_text(str(time.time()))
        except OSError:
            pass
        if self.agent:
            self.stop(None)
        self.say("Okay, I stopped.")
        self.status.title = "Stopped (Escape pressed 3 times)"

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

    # --- the bar and voice -----------------------------------------------------

    def bar(self):
        if self.prompt_bar is None:
            from .prompt_bar import PromptBar
            self.prompt_bar = PromptBar.create(self)
        return self.prompt_bar

    def bar_listening(self) -> bool:
        return self.prompt_bar is not None and self.prompt_bar.listening

    def open_bar(self, listening: bool | None = None):
        bar = self.bar()
        if not bar.is_visible():
            bar.show(NSWorkspace.sharedWorkspace().frontmostApplication())
        if self.settings["open_listening"] if listening is None else listening:
            self.start_listening()

    def on_gesture(self, gesture: str | None):
        """Double-tap opens or closes the bar; double-tap and hold listens while held, then sends."""
        if gesture == "double_tap":
            self.shortcut_pressed()
        elif gesture == "hold_start":
            self.open_bar(listening=False)
            self.start_listening()
        elif gesture == "hold_end" and self.bar_listening():
            self.stop_listening(send=True)

    def shortcut_pressed(self):
        """The shortcut opens the bar, and pressing it again closes it."""
        if self.prompt_bar is not None and self.prompt_bar.is_visible():
            if self.prompt_bar.listening:
                self.stop_listening(send=False)
            self.prompt_bar.hide()
        else:
            self.open_bar()

    def start_listening(self):
        bar = self.bar()
        if not bar.is_visible():
            bar.show()
        bar.set_listening(True)
        self.direct = True
        self.ensure_voice(self._listen)

    def stop_listening(self, send: bool = True):
        """End the utterance. With send, the final transcript arrives and runs; without, it's dropped."""
        if self.listener is not None and self.listener.listening and send:
            self.listener._finish()
            return
        self.direct = False
        if self.prompt_bar is not None:
            self.prompt_bar.set_listening(False)
        if not self.settings["hands_free"]:
            self._drop_listener()

    def _listen(self):
        if self.listener is None or self.listener.listening or self.awaiting_confirm:
            return
        self.listener.start()
        if self.listener.listening:
            if self.direct and self.agent is None:
                self.status.title = "Listening…"

    def on_speech(self, kind, text):
        from . import voice

        listening = self.bar_listening()
        if kind == "status":
            self.status.title = _short(text, 60)
            return
        if kind == "error":
            self.direct = False
            self.status.title = f"Voice: {_short(text, 50)}"
            if self.prompt_bar is not None:
                self.prompt_bar.set_listening(False)
            if not self.settings["hands_free"]:
                self._drop_listener()
            return
        if say.is_echo(text):
            return  # our own voice coming back through the mic
        if kind == "partial":
            if say.speaking():
                say.stop()  # the user is talking over us
            if listening:
                self.prompt_bar.show_transcript(text)
            elif self.direct or voice.strip_wake_word(text) is not None or (self.agent and voice.is_stop(text)):
                self.status.title = _short(text, 60)
            return

        # a final utterance
        direct, self.direct = self.direct, False
        command = voice.strip_wake_word(text)
        if command is None and (direct or listening or (self.agent and voice.is_stop(text))):
            command = text.strip()
        if not command:
            if listening:
                self.direct = True  # nothing heard: keep listening
            elif not self.settings["hands_free"]:
                self._drop_listener()
            return
        if voice.is_stop(command):
            if self.agent:
                self.stop(None)
                self.say("Stopping.")
            if self.prompt_bar is not None:
                self.prompt_bar.hide()
            if not self.settings["hands_free"]:
                self._drop_listener()
            return

        self.log(f"Heard: {command}")
        self.voice_task = True
        if self.agent:
            # A new instruction mid-task replaces the current one.
            self.pending = command
            if self.prompt_bar is not None:
                self.prompt_bar.hide()
            self.stop(None)
            self.say("Okay, switching.")
            return
        self.say(replies.ack(command))
        if listening and self.prompt_bar.is_visible():
            self.prompt_bar.set_listening(False)
            self.prompt_bar.show_transcript(command, final=True)  # shows it, then sends via start()
        else:
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
        if self.settings.get("speak_replies") or self.voice_task:
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
        if self.listener is not None:
            self.listener.tick()
        listening = self.bar_listening()
        if (self.settings["hands_free"] or listening) and self.listener is not None and not self.listener.listening and not self.awaiting_confirm:
            if listening:
                self.direct = True
                self._listen()
            elif not say.speaking():
                self._listen()
        elif not self.settings["hands_free"] and not listening and self.listener is not None and not self.listener.listening:
            self._drop_listener()

    # --- running -------------------------------------------------------------

    def start(self, task: str):
        from .agent import Agent

        self.direct = False
        if self.prompt_bar is not None:
            self.prompt_bar.hide(restore_focus=False)
        if not self.settings["hands_free"]:
            self._drop_listener()

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
        self.refresh()
        self.last_task, self.last_log = task, deque(maxlen=80)
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
        self.last_log.append(line)
        LOG.parent.mkdir(parents=True, exist_ok=True)
        self._log_writes = getattr(self, "_log_writes", 0) + 1
        if self._log_writes % 200 == 0:
            rotate(LOG)
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
        detector = getattr(self, "_detector", None)
        if detector is not None:
            self.on_gesture(detector.tick(time.monotonic()))
        self._voice_tick()
        self._icon_tick()
        while not self.events.empty():
            kind, payload = self.events.get()
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
            if kind == "skill":
                self.build_skills_menu()
                rumps.alert("Skills", payload)
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
                if not self.settings["hands_free"]:
                    self._drop_listener()
                self.status.title = "Idle"
                self.last.title = f"Last: {_short(payload, 50)}"
                self.last._menuitem.setHidden_(False)
                self.refresh()
                self.refresh_usage()
                if self.pending:
                    task, self.pending = self.pending, None
                    self.start(task)
                    continue
                if self.update_ready:
                    self.finish_update()
                if self.voice_task:
                    self.say(replies.result(payload, self.last_task))
                    self.voice_task = False
                rumps.notification("Computer Use", "Task finished", payload)


def _fill(menu, items):
    for item in items:
        menu.add(rumps.separator if item is None else item)


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


def _typed_by_an_agent(event) -> bool:
    """Whether a key event was posted by this app or by cu-mcp rather than typed by a person."""
    import os
    import Quartz
    try:
        pid = Quartz.CGEventGetIntegerValueField(event.CGEvent(), Quartz.kCGEventSourceUnixProcessID)
    except Exception:
        return False
    if pid == os.getpid():
        return True
    if pid <= 0:
        return False
    name = subprocess.run(["ps", "-p", str(pid), "-o", "comm="], capture_output=True, text=True).stdout.strip()
    return name.endswith("cu-mcp")


def _short(s: str, n: int = 40) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _open(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    NSWorkspace.sharedWorkspace().openFile_(str(path))


def main():
    from . import indicator
    load_keys()
    indicator.enable()
    MenuBar().run()


if __name__ == "__main__":
    main()
