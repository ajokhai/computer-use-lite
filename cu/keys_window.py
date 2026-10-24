"""Models and Keys: one window listing every model provider with its status, and a button that
asks for just that provider's key (or, for a local server, its address and model).

Opens from Settings › API Keys…, Mode › Vision Model › Add a Model…, and the setup window."""

import os

import objc
from AppKit import (
    NSAlert,
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
    NSSecureTextField,
    NSTextField,
    NSView,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSObject

from .config import DEFAULTS, load_keys, load_settings, save_settings, set_key

WIDTH = 540.0
ROW_H = 58.0
PAD = 24.0
FIELD_W = 340.0

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


def _symbol(name: str):
    img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
    return img.imageWithSymbolConfiguration_(NSImageSymbolConfiguration.configurationWithPointSize_weight_(18, 0.0))


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
        height = 116 + ROW_H * len(PROVIDERS) + 74
        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, WIDTH, height), NSWindowStyleMaskTitled | NSWindowStyleMaskClosable, 2, False)
        self.panel.setTitle_("Models and Keys")
        self.panel.setReleasedWhenClosed_(False)
        content = self.panel.contentView()
        y = height - PAD

        title = _label("Models and Keys", 20, NSColor.labelColor(), 0.3)
        title.setFrame_(NSMakeRect(PAD, y - 26, WIDTH - 2 * PAD, 26))
        content.addSubview_(title)
        y -= 34
        intro = _label("Add any one to run tasks from the bar. Keys are kept in your macOS Keychain. "
                       "Choose which model works under Mode › Vision Model.", 13, NSColor.secondaryLabelColor())
        intro.setFrame_(NSMakeRect(PAD, y - 36, WIDTH - 2 * PAD, 36))
        content.addSubview_(intro)
        y -= 50

        for i, (row, name, desc, _key, _model) in enumerate(PROVIDERS):
            y -= ROW_H
            if i:
                sep = NSBox.alloc().initWithFrame_(NSMakeRect(PAD + 38, y + ROW_H, WIDTH - 2 * PAD - 38, 1))
                sep.setBoxType_(2)  # separator
                content.addSubview_(sep)
            self.rows[row] = self._row(content, y, row, name, desc)

        self.done = NSButton.buttonWithTitle_target_action_("Done", self, "doneClicked:")
        self.done.setKeyEquivalent_("\r")
        self.github = NSButton.buttonWithTitle_target_action_("GitHub Token…", self, "githubClicked:")
        self.github.setToolTip_("Only needed for updates from a private repo when the gh command isn't signed in")
        for b in (self.done, self.github):
            b.sizeToFit()
            content.addSubview_(b)
        dw = max(self.done.frame().size.width, 96)
        self.done.setFrame_(NSMakeRect(WIDTH - PAD - dw, 18, dw, 32))
        self.github.setFrame_(NSMakeRect(PAD, 18, self.github.frame().size.width, 32))
        self.refresh()

    @objc.python_method
    def _row(self, content, y, row, name, desc):
        icon = NSImageView.alloc().initWithFrame_(NSMakeRect(PAD, y + ROW_H / 2 - 12, 24, 24))
        content.addSubview_(icon)
        x = PAD + 38
        head = _label(name, 13, NSColor.labelColor(), 0.3)
        head.sizeToFit()
        head.setFrame_(NSMakeRect(x, y + ROW_H - 24, head.frame().size.width, 18))
        content.addSubview_(head)
        state = _label("", 11, NSColor.secondaryLabelColor(), 0.23)
        state.setFrame_(NSMakeRect(x + head.frame().size.width + 8, y + ROW_H - 23, 180, 16))
        content.addSubview_(state)
        button = NSButton.buttonWithTitle_target_action_("Change…", self, "editClicked:")
        button.setIdentifier_(row)
        button.sizeToFit()
        bw = max(button.frame().size.width, 96)
        button.setFrame_(NSMakeRect(WIDTH - PAD - bw, y + ROW_H / 2 - 16, bw, 32))
        content.addSubview_(button)
        detail = _label(desc, 11, NSColor.secondaryLabelColor())
        detail.setFrame_(NSMakeRect(x, y + 4, WIDTH - x - PAD - bw - 12, 30))
        content.addSubview_(detail)
        return {"icon": icon, "state": state, "button": button}

    @objc.python_method
    def refresh(self):
        s = load_settings()
        for row, *_ in PROVIDERS:
            ok, words = status(row, s)
            half = not ok and words != "Not set"  # a key but no model yet
            r = self.rows[row]
            r["icon"].setImage_(_symbol("checkmark.circle.fill" if ok else "exclamationmark.circle.fill" if half else "circle"))
            r["icon"].setContentTintColor_(NSColor.systemGreenColor() if ok else
                                           NSColor.systemOrangeColor() if half else NSColor.tertiaryLabelColor())
            r["state"].setStringValue_(words)
            r["state"].setTextColor_(NSColor.secondaryLabelColor() if ok else NSColor.tertiaryLabelColor())
            r["button"].setTitle_("Change…" if words != "Not set" else ("Set Up…" if row == "local" else "Add…"))

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
