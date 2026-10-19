"""Menu bar app: a status icon with tick-able options. No windows except small prompts."""

import queue
import threading

import rumps
from AppKit import NSApplication, NSImage, NSWorkspace

from . import integration, usage
from .config import KEYS, LOG, get_key, load_keys, load_settings, save_settings, set_key

IDLE_ICON, BUSY_ICON, WARN_ICON = "cursorarrow.rays", "cursorarrow.click.2", "exclamationmark.triangle"


class MenuBar(rumps.App):
    def __init__(self):
        super().__init__("Computer Use", title="CU", quit_button=None)
        self.settings = load_settings()
        integration.refresh_connections()
        self.events: queue.Queue = queue.Queue()
        self.agent = None

        self.status = rumps.MenuItem("Idle")
        self.new = rumps.MenuItem("New Task…", callback=self.new_task, key="n")
        self.stop_item = rumps.MenuItem("Stop", callback=None, key=".")

        self.usage_menu = rumps.MenuItem("Usage")
        self.modes = {
            "auto": rumps.MenuItem("Auto — best available", callback=lambda _: self.set_mode("auto")),
            "hybrid": rumps.MenuItem("Hybrid — Gemini plans, Jev acts", callback=lambda _: self.set_mode("hybrid")),
            "jev": rumps.MenuItem("Jev only — cheapest", callback=lambda _: self.set_mode("jev")),
            "gemini": rumps.MenuItem("Gemini only", callback=lambda _: self.set_mode("gemini")),
        }
        mode_menu = rumps.MenuItem("Mode")
        for item in self.modes.values():
            mode_menu.add(item)

        self.toggles = {
            "awake": rumps.MenuItem("Keep Mac awake while working", callback=self.toggle),
            "auto_switch": rumps.MenuItem("Switch models automatically when one runs out", callback=self.toggle),
            "confirm_all": rumps.MenuItem("Ask before every action", callback=self.toggle),
            "allow_risky": rumps.MenuItem("Don't ask before send / buy / delete", callback=self.toggle),
        }

        settings_menu = rumps.MenuItem("Settings")
        self.login = rumps.MenuItem("Open at Login", callback=self.toggle_login)
        connect_menu = rumps.MenuItem("Connect to")
        self.clients = {name: rumps.MenuItem(name, callback=lambda _, n=name: self.toggle_client(n))
                        for name in integration.CLIENTS}
        for item in self.clients.values():
            connect_menu.add(item)
        for item in (
            rumps.MenuItem("API Keys…", callback=self.edit_keys),
            None,
            self.login,
            connect_menu,
            None,
            rumps.MenuItem("Grant Accessibility…", callback=lambda _: integration.open_privacy_settings("Accessibility")),
            rumps.MenuItem("Grant Screen Recording…", callback=lambda _: integration.open_privacy_settings("ScreenCapture")),
            None,
            rumps.MenuItem("Open Log", callback=lambda _: _open(LOG)),
        ):
            settings_menu.add(item) if item is not None else settings_menu.add(rumps.separator)

        self.last = rumps.MenuItem("No tasks yet")
        self.menu = [
            self.status, self.new, self.stop_item, None,
            self.usage_menu, mode_menu, None,
            *self.toggles.values(), None,
            self.last, settings_menu, None,
            rumps.MenuItem("Quit Computer Use", callback=rumps.quit_application, key="q"),
        ]
        self.refresh()
        self.refresh_usage()
        rumps.Timer(self.pump, 0.15).start()
        rumps.Timer(lambda _: self.refresh_usage(), 30).start()
        rumps.Timer(self._first_paint, 0.05).start()

    # --- state ---------------------------------------------------------------

    def _first_paint(self, timer):
        timer.stop()
        NSApplication.sharedApplication().setActivationPolicy_(1)  # menu bar only, no Dock icon
        self.set_icon(IDLE_ICON)

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
        for key, item in self.toggles.items():
            item.state = self.settings[key]
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
            item = rumps.MenuItem(line if has_key else f"{usage.pretty(provider)}: no API key",
                                  callback=lambda _, p=provider: self.set_limit(p))
            self.usage_menu.add(item)
            any_out |= has_key and not usage.status(provider)["available"]
        self.usage_menu.add(rumps.separator)
        self.usage_menu.add(rumps.MenuItem("Click a line to set its limit from your dashboard", callback=None))
        self.usage_menu.add(rumps.MenuItem("Clear “out of quota” flags", callback=self.clear_flags))
        if self.agent is None:
            self.set_icon(WARN_ICON if any_out else IDLE_ICON)

    def set_mode(self, mode):
        self.settings["mode"] = mode
        save_settings(self.settings)
        self.refresh()

    def toggle(self, item):
        key = next(k for k, v in self.toggles.items() if v is item)
        self.settings[key] = not self.settings[key]
        save_settings(self.settings)
        self.refresh()

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
        labels = {"GEMINI_API_KEY": "Gemini API key (aistudio.google.com/apikey)",
                  "TYPESAFE_API_KEY": "Jev API key (TypeSafe dashboard)"}
        for name in KEYS:
            current = get_key(name)
            hint = "A key is saved. Leave it as is to keep it, or clear the field to remove it." if current else "Optional."
            value = self._prompt("API Keys", f"{labels[name]}\n{hint}", default=current, secure=True)
            if value is None:
                return
            if value != current:
                set_key(name, value)
        load_keys()
        self.refresh_usage()

    def set_limit(self, provider: str):
        limits = self.settings["limits"].setdefault(provider, {})
        if provider == "jev":
            v = self._prompt("Jev budget", "Monthly Jev budget in USD (blank = no limit):",
                             str(limits.get("usd_per_month") or ""))
            if v is None:
                return
            limits["usd_per_month"] = float(v) if v else None
            d = self._prompt("Jev budget", "Day of the month your Jev billing resets (1–28):",
                             str(limits.get("reset_day") or 1))
            if d:
                limits["reset_day"] = max(1, min(28, int(d)))
        else:
            v = self._prompt(usage.pretty(provider),
                             "Requests per day for this model, from AI Studio › Usage & limits (blank = unknown).\n"
                             "Resets at midnight Pacific time.", str(limits.get("requests_per_day") or ""))
            if v is None:
                return
            limits["requests_per_day"] = int(v) if v else None
        save_settings(self.settings)
        self.refresh_usage()

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
                confirm=self.confirm, log=self.log,
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
        while not self.events.empty():
            kind, payload = self.events.get()
            if kind == "status" and self.agent and not self.agent.stop.is_set():
                self.status.title = _short(payload, 60)
            elif kind == "confirm":
                msg, done, answer = payload
                NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
                answer.append(rumps.alert("Computer Use", msg, ok="Allow", cancel="Stop") == 1)
                done.set()
            elif kind == "finished":
                self.agent = None
                self.status.title = "Idle"
                self.last.title = f"Last: {_short(payload, 50)}"
                self.refresh()
                self.refresh_usage()
                rumps.notification("Computer Use", "Task finished", payload)


def _env_key(provider: str) -> bool:
    import os
    names = ("TYPESAFE_API_KEY", "JEV_API_KEY") if provider == "jev" else ("GEMINI_API_KEY", "GOOGLE_API_KEY")
    return any(os.environ.get(n) for n in names)


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
