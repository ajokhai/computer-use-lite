"""Models and Keys: one window listing every model provider with its status, and a button that
asks for just that provider's key (or, for a local server, its address and model).

Opens from Settings › API Keys…, Mode › Vision Model › Add a Model…, and the setup window."""

import os

import objc
from AppKit import (
    NSAlert,
    NSApplication,
    NSColor,
    NSFont,
    NSImage,
    NSImageSymbolConfiguration,
    NSMakeRect,
    NSPanel,
    NSSecureTextField,
    NSTextField,
    NSView,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskFullSizeContentView,
    NSWindowStyleMaskTitled,
)
from Foundation import NSObject

from . import ui
from .config import DEFAULTS, load_keys, load_settings, save_settings, set_key

PAD = 24.0
FIELD_W = 340.0

CARD_W, CARD_H = 262.0, 168.0
WHERE = {"jev": "Key from your TypeSafe dashboard.", "claude": "Key from platform.claude.com.",
         "openai": "Key from platform.openai.com.", "gemini": "Key from aistudio.google.com.",
         "openrouter": "Key from openrouter.ai, then pick a model.", "local": "Ollama, LM Studio or any "
         "OpenAI-compatible server. No key needed."}
ROLES = {"jev": "Cheapest actions", "claude": "Vision model", "openai": "Vision model",
         "gemini": "Vision model and voices", "openrouter": "Any model it offers", "local": "Runs on this Mac"}

# (row id, title, what it's for, Keychain key, settings key for the model name)
PROVIDERS = [
    ("jev", "Jev", "Cheapest actions. Key from your TypeSafe dashboard.",
     "TYPESAFE_API_KEY", None),
    ("claude", "Claude", "Vision model by Anthropic. Key from platform.claude.com.", "ANTHROPIC_API_KEY", None),
    ("openai", "OpenAI", "Vision model by OpenAI. Key from platform.openai.com.", "OPENAI_API_KEY", None),
    ("gemini", "Gemini", "Vision model and Gemini voices. Key from aistudio.google.com.", "GEMINI_API_KEY", None),
    ("openrouter", "OpenRouter", "Any model OpenRouter offers. Key from openrouter.ai.", "OPENROUTER_API_KEY",
     "openrouter_model"),
    ("local", "Local Model", "A model on this Mac (Ollama, LM Studio…). No key needed.", None,
     "local_model"),
]
PROVIDERS_BY_ID = {p[0]: p for p in PROVIDERS}


def _saved(key: str) -> bool:
    """Keys are loaded from the Keychain into the environment at launch (and on every change here),
    so this needs no Keychain lookup, which could prompt."""
    return bool(os.environ.get(key) or (key == "GEMINI_API_KEY" and os.environ.get("GOOGLE_API_KEY")))


def status(row: str, settings: dict | None = None) -> tuple[bool, str]:
    """(set up, the words shown): "Added", "Not set", or for model-based rows the model's name."""
    s = settings or load_settings()
    _id, _title, _desc, key, model_key = next(p for p in PROVIDERS if p[0] == row)
    has_key = key is None or _saved(key)
    model = s.get(model_key) if model_key else None
    if model_key and has_key and model:
        return True, model
    if key and has_key:
        return (False, "Key added, no model") if model_key else (True, "Added")
    return False, "Not set"


def _label(text: str, size: float, color, weight: float = 0.0) -> NSTextField:
    f = NSTextField.wrappingLabelWithString_(text)
    f.setFont_(NSFont.systemFontOfSize_weight_(size, weight))
    f.setTextColor_(color)
    f.setSelectable_(False)
    return f


def _form(rows: list[tuple[str, NSTextField, str]]) -> NSView:
    """An alert accessory: a label, a field and an optional hint per row."""
    row_h = 68.0
    view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, FIELD_W, row_h * len(rows)))
    for i, (title, field, hint) in enumerate(rows):
        y = (len(rows) - 1 - i) * row_h
        head = _label(title, 12, NSColor.labelColor(), 0.3)
        head.setFrame_(NSMakeRect(0, y + 50, FIELD_W, 16))
        field.setFrame_(NSMakeRect(0, y + 24, FIELD_W, 24))
        note = _label(hint, 11, NSColor.secondaryLabelColor())
        note.setFrame_(NSMakeRect(0, y + 8, FIELD_W, 14))
        for v in (head, field, note):
            view.addSubview_(v)
    return view


def _ask(title: str, info: str, rows: list[tuple[str, NSTextField, str]], remove: bool = False) -> str:
    """Run a small form; returns "save", "remove" or "cancel"."""
    alert = NSAlert.alloc().init()
    alert.setMessageText_(title)
    alert.setInformativeText_(info)
    alert.addButtonWithTitle_("Save")
    alert.addButtonWithTitle_("Cancel")
    if remove:
        alert.addButtonWithTitle_("Remove").setHasDestructiveAction_(True)
    alert.setIcon_(NSImage.imageWithSystemSymbolName_accessibilityDescription_("key.fill", None)
                   .imageWithSymbolConfiguration_(NSImageSymbolConfiguration.configurationWithPointSize_weight_(32, 0.0)))
    alert.setAccessoryView_(_form(rows))
    alert.window().setInitialFirstResponder_(rows[0][1])
    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    choice = alert.runModal()
    return {1000: "save", 1002: "remove"}.get(choice, "cancel")


def _field(value: str = "", placeholder: str = "", secure: bool = False) -> NSTextField:
    f = (NSSecureTextField if secure else NSTextField).alloc().initWithFrame_(NSMakeRect(0, 0, FIELD_W, 24))
    f.setStringValue_(value)
    f.setPlaceholderString_(placeholder)
    return f


class KeysWindow(NSObject):
    def initWithApp_(self, app):
        self = objc.super(KeysWindow, self).init()
        if self is None:
            return None
        self.app = app  # the menu bar app, refreshed after every change
        self.rows = {}
        self._build()
        return self

    @objc.python_method
    @classmethod
    def create(cls, app=None):
        return cls.alloc().initWithApp_(app)

    @objc.python_method
    def _build(self):
        rows = (len(PROVIDERS) + 1) // 2
        grid_h = rows * CARD_H + (rows - 1) * ui.GAP
        width = 2 * PAD + 2 * CARD_W + ui.GAP
        height = 112 + grid_h + 76
        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, width, height), NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
            | NSWindowStyleMaskFullSizeContentView, 2, False)
        self.panel.setTitle_("Models and Keys")
        self.panel.setTitlebarAppearsTransparent_(True)
        self.panel.setTitleVisibility_(1)  # hidden: the big title below says it
        self.panel.setBackgroundColor_(ui.BACKGROUND)
        self.panel.setReleasedWhenClosed_(False)
        content = self.panel.contentView()

        ui.place(content, ui.label("Models and Keys", 24, weight=0.4), PAD, height - 72)
        ui.place(content, ui.label("Add any one to run tasks from the bar. Keys stay in your macOS Keychain.",
                                   13, NSColor.secondaryLabelColor()), PAD, height - 96)
        self.grid = NSView.alloc().initWithFrame_(NSMakeRect(PAD, 76, 2 * CARD_W + ui.GAP, grid_h))
        content.addSubview_(self.grid)

        self.done = ui.capsule_button("Done", self, "doneClicked:", primary=True)
        self.done.setKeyEquivalent_("\r")
        ui.place(content, self.done, width - PAD - self.done.frame().size.width, 24,
                 self.done.frame().size.width, self.done.frame().size.height)
        self.github = ui.capsule_button("GitHub Token…", self, "githubClicked:")
        self.github.setToolTip_("Only needed for updates from a private repo when the gh command isn't signed in")
        ui.place(content, self.github, PAD, 24, self.github.frame().size.width, self.github.frame().size.height)
        self.refresh()

    @objc.python_method
    def _in_use(self, s: dict) -> str | None:
        """The vision provider tasks would use now: the chosen model's, or Auto's first ready one."""
        from . import vision
        choice = s.get("vision_model") or "auto"
        if choice != "auto":
            return vision.provider_of(choice)
        return next((p for p, _ in vision.order(s) if vision.has_key(p)), None)

    @objc.python_method
    def _card(self, row: str, name: str, s: dict, in_use: bool) -> NSView:
        from . import usage, vision
        ok, words = status(row, s)
        half = not ok and words != "Not set"
        c = ui.card(NSMakeRect(0, 0, CARD_W, CARD_H), ui.INVERSE if in_use else ui.CARD)
        if in_use:  # in dark mode every card is dark, so the one in use also gets a faint ring
            c.setBorderWidth_(1.5)
            c.setBorderColor_(ui.INVERSE_RING)
        v = c.contentView()
        ink = ui.INVERSE_TEXT if in_use else NSColor.labelColor()
        soft = ui.INVERSE_SECONDARY if in_use else NSColor.secondaryLabelColor()
        pad = 18.0

        ui.place(v, ui.label(name, 15, ink, 0.4), pad, CARD_H - 36)
        models = vision.models(row, s) if row != "jev" else []
        sub = " · ".join(usage.pretty(m).removeprefix(f"{name} ").removeprefix("Local ").removeprefix("OpenRouter ")
                         for m in models) if ok and models else ROLES[row]
        ui.place(v, ui.label(sub, 12, soft), pad, CARD_H - 56, CARD_W - 2 * pad, 16)

        tag, tint = (("In Use", NSColor.systemGreenColor()) if in_use else ("Ready", NSColor.systemGreenColor()) if ok
                     else ("Needs Model", NSColor.systemOrangeColor()) if half else ("Not Set", NSColor.tertiaryLabelColor()))
        p = ui.pill(tag, tint)
        ui.place(v, p, CARD_W - pad - p.frame().size.width, CARD_H - 34, p.frame().size.width, p.frame().size.height)

        if ok or half:
            if row == "jev":
                big, caption = f"${usage.status('jev')['used']:.2f}", "this month"
            else:
                big, caption = str(sum(usage.status(m)["requests"] for m in models)), "requests today"
            limits = [usage.status(m) for m in (models or ["jev"])]
            capped = [st for st in limits if st["limit"]]
            if capped:  # how much of the tightest limit is used, across the card
                worst = max(capped, key=lambda st: st["used"] / st["limit"])
                v.addSubview_(ui.gradient_bar(NSMakeRect(pad, 88, CARD_W - 2 * pad, 5), worst["used"] / worst["limit"]))
                left = worst["limit"] - worst["used"]
                caption += f" · {'$' + format(left, '.2f') if row == 'jev' else int(left)} left"
            ui.place(v, ui.label(big, 30, ink, 0.3, digits=True), pad, 40)
            ui.place(v, ui.label(caption, 12, soft), pad, 24)
        else:
            ui.place(v, ui.label(WHERE[row], 12, soft, wrap=True), pad, 56, CARD_W - 2 * pad, 34)

        title = "Change" if ok or half else ("Set Up" if row == "local" else "Add Key")
        b = ui.capsule_button(title, self, "editClicked:", primary=not (ok or half), on_dark=in_use)
        b.setIdentifier_(row)
        ui.place(v, b, CARD_W - pad - b.frame().size.width, 18, b.frame().size.width, b.frame().size.height)
        return c

    @objc.python_method
    def refresh(self):
        s = load_settings()
        in_use = self._in_use(s)
        for sub in list(self.grid.subviews()):
            sub.removeFromSuperview()
        rows = (len(PROVIDERS) + 1) // 2
        for i, (row, name, *_rest) in enumerate(PROVIDERS):
            col, line = i % 2, i // 2
            c = self._card(row, name, s, row == in_use)
            c.setFrameOrigin_((col * (CARD_W + ui.GAP), (rows - 1 - line) * (CARD_H + ui.GAP)))
            self.grid.addSubview_(c)

    @objc.python_method
    def _changed(self):
        load_keys()
        self.refresh()
        if self.app is not None:
            self.app.settings = load_settings()
            self.app.refresh()
            self.app.refresh_usage()

    # --- editing one provider ----------------------------------------------------

    @objc.python_method
    def edit(self, row: str):
        _id, name, desc, key, model_key = next(p for p in PROVIDERS if p[0] == row)
        s = load_settings()
        saved = _saved(key) if key else False
        rows, fields = [], {}
        if key:
            fields["key"] = _field(placeholder="Saved; paste a new key to replace it" if saved else "Paste the key",
                                   secure=True)
            rows.append((f"{name} API Key", fields["key"], "Stored in your macOS Keychain."))
        if row == "local":
            fields["url"] = _field(s.get("local_url") or DEFAULTS["local_url"], DEFAULTS["local_url"])
            rows.append(("Server Address", fields["url"], "Ollama: localhost:11434/v1 · LM Studio: localhost:1234/v1"))
        if model_key:
            hint = "A vision model's id from openrouter.ai/models." if row == "openrouter" else self._local_hint(s)
            fields["model"] = _field(s.get(model_key) or "", "Model name")
            rows.append(("Model", fields["model"], hint))
        has_any = saved or bool(model_key and s.get(model_key))
        choice = _ask(f"{name}", desc, rows, remove=has_any)
        if choice == "cancel":
            return
        if choice == "remove":
            if key:
                set_key(key, "")
            if model_key:
                self._set_model(s, row, model_key, "")
        else:
            value = str(fields["key"].stringValue()).strip() if key else ""
            if value:
                set_key(key, value)
            if row == "local":
                s["local_url"] = str(fields["url"].stringValue()).strip() or DEFAULTS["local_url"]
            if model_key:
                self._set_model(s, row, model_key, str(fields["model"].stringValue()).strip())
        save_settings(s)
        self._changed()

    @objc.python_method
    def _local_hint(self, s: dict) -> str:
        from .vision.compat import list_models
        found = list_models(s.get("local_url") or DEFAULTS["local_url"])
        return f"On the server: {', '.join(found[:6])}" if found else "The server isn't answering; type the name anyway."

    @objc.python_method
    def _set_model(self, s: dict, row: str, model_key: str, name: str):
        old = s.get(model_key)
        s[model_key] = name
        if old and s.get("vision_model") == f"{row}:{old}":
            s["vision_model"] = f"{row}:{name}" if name else "auto"

    # --- buttons ---------------------------------------------------------------

    def editClicked_(self, sender):
        self.edit(str(sender.identifier()))

    def githubClicked_(self, sender):
        saved = _saved("GITHUB_TOKEN")
        field = _field(placeholder="Saved; paste a new token to replace it" if saved else "Paste a token", secure=True)
        choice = _ask("GitHub Token", "Only for updates from a private repo when the gh command isn't signed in.",
                      [("Token", field, "Stored in your macOS Keychain.")], remove=saved)
        if choice == "save" and str(field.stringValue()).strip():
            set_key("GITHUB_TOKEN", str(field.stringValue()).strip())
        elif choice == "remove":
            set_key("GITHUB_TOKEN", "")
        self._changed()

    def doneClicked_(self, sender):
        self.panel.orderOut_(None)

    @objc.python_method
    def show(self):
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.refresh()
        self.panel.center()
        self.panel.makeKeyAndOrderFront_(None)


_open = None


def show(app=None):
    """Show the window (one at a time)."""
    global _open
    if _open is None:
        _open = KeysWindow.create(app)
    _open.show()
