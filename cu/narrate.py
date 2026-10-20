"""Short spoken phrases for what the agent is doing ("Opening Notes", "Typing groceries")."""


def _clip(text: str, words: int = 8) -> str:
    parts = str(text).split()
    return " ".join(parts[:words]) + ("…" if len(parts) > words else "")


def step(s: dict) -> str:
    """A planned hybrid step."""
    kind = s.get("kind")
    target = _clip(s.get("target", ""), 5)
    return {
        "open_app": f"Opening {s.get('app', '')}",
        "open_url": "Opening the page",
        "click": f"Clicking {target}" if target else "Clicking",
        "double_click": f"Opening {target}" if target else "Double-clicking",
        "type": f"Typing {_clip(s.get('text', ''), 6)}",
        "hotkey": "Pressing " + " ".join(s.get("keys", [])),
        "scroll": f"Scrolling {s.get('direction', 'down')}",
        "wait": "Waiting a moment",
    }.get(kind, "")


def jev_op(op: str, element_label: str = "", text: str = "", app: str = "") -> str:
    label = _clip(element_label, 5)
    return {
        "CLICK": f"Clicking {label}" if label else "Clicking",
        "DOUBLE_CLICK": f"Opening {label}" if label else "Double-clicking",
        "TYPE": f"Typing {_clip(text, 6)}",
        "PRESS_ENTER": "Pressing return",
        "PRESS_ESCAPE": "Closing that",
        "PRESS_TAB": "",
        "NEW_ITEM": "Making a new one",
        "COPY": "Copying to clipboard",
        "SELECT_ALL": "Selecting all",
        "CLOSE_WINDOW": "Closing window",
        "QUIT_APP": f"Quitting {app}" if app else "Quitting app",
        "WAIT": "",
    }.get(op, "")


def gemini_action(name: str, args: dict) -> str:
    """Gemini's own one-line reason for the action when it gives one."""
    if args.get("intent"):
        return _clip(args["intent"], 14)
    if name == "type":
        return f"Typing {_clip(args.get('text', ''), 6)}"
    if name == "open_app":
        return f"Opening {args.get('app_name', '')}"
    if name == "scroll":
        return f"Scrolling {args.get('direction', 'down')}"
    return ""
