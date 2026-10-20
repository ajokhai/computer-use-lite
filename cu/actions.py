"""Mouse and keyboard actions in screen points. Slam the mouse into a corner to abort (pyautogui failsafe)."""

import subprocess
import time

import pyautogui

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.01

# Gemini/Playwright-style key names -> pyautogui names.
KEY_ALIASES = {
    "enter": "enter", "return": "enter", "escape": "esc", "esc": "esc",
    "control": "ctrl", "ctrl": "ctrl", "meta": "command", "cmd": "command",
    "command": "command", "super": "command", "option": "option", "alt": "option",
    "arrowup": "up", "arrowdown": "down", "arrowleft": "left", "arrowright": "right",
    "pageup": "pageup", "pagedown": "pagedown", "backspace": "backspace",
    "delete": "delete", "tab": "tab", "space": "space", "shift": "shift",
}


def key_name(k: str) -> str:
    k = k.strip()
    return KEY_ALIASES.get(k.lower().replace("_", ""), k.lower())


def move_to(x, y, duration=0.12):
    """Smoothly glide the cursor across the screen so the user can follow along."""
    try:
        pyautogui.moveTo(x, y, duration=duration, tween=pyautogui.easeOutQuad)
    except Exception:
        pyautogui.moveTo(x, y)


def click(x, y, button="left", clicks=1):
    from . import indicator
    move_to(x, y, duration=0.12)
    indicator.show_click_ring(x, y)
    pyautogui.click(x, y, clicks=clicks, interval=0.04, button=button)


def paste():
    """Paste the clipboard content (press Command-V)."""
    pyautogui.hotkey("command", "v")
    time.sleep(0.08)


def screenshot_to_clipboard(rect=None):
    """Capture the screen or a region and copy it directly to the clipboard."""
    cmd = ["screencapture", "-c"]
    if rect:
        x, y, w, h = rect
        cmd.append(f"-R{x},{y},{w},{h}")
    subprocess.run(cmd, check=True)
    time.sleep(0.1)


def type_text(text: str, press_enter=False, clear=False):
    if clear:
        pyautogui.hotkey("command", "a")
        pyautogui.press("backspace")
    # Paste via the clipboard: fast, and handles non-ASCII that typewrite cannot.
    old = subprocess.run(["pbpaste"], capture_output=True).stdout
    subprocess.run(["pbcopy"], input=text.encode(), check=True)
    pyautogui.hotkey("command", "v")
    time.sleep(0.05)
    subprocess.run(["pbcopy"], input=old)
    if press_enter:
        pyautogui.press("return")


def create_new_document(app_name: str | None = None) -> bool:
    """Safely create a new document by targeting explicit UI buttons first, falling back to Command-N."""
    try:
        from .screen import ax_elements
        app, els = ax_elements(limit=100)
        new_btns = [e for e in els if e.role in ("AXButton", "AXMenuButton") and 
                    any(phrase in e.label.lower() for phrase in ("new note", "new message", "new document", "compose"))]
        if new_btns:
            click(new_btns[0].x, new_btns[0].y)
            time.sleep(0.3)
            return True
    except Exception:
        pass
    hotkey(["command", "n"])
    time.sleep(0.3)
    return True


def hotkey(keys):
    keys = [key_name(k) for k in keys]
    if len(keys) == 1:
        pyautogui.press(keys[0])
    else:
        pyautogui.hotkey(*keys)


def scroll(x, y, direction="down", amount=300):
    from . import indicator
    move_to(x, y, duration=0.1)
    indicator.show_click_ring(x, y)
    # pyautogui scroll units are lines on macOS; ~10px each.
    lines = max(1, int(amount / 10))
    if direction in ("up", "down"):
        pyautogui.scroll(lines if direction == "up" else -lines)
    else:
        pyautogui.hscroll(lines if direction == "right" else -lines)


def drag(x1, y1, x2, y2):
    from . import indicator
    move_to(x1, y1, duration=0.1)
    indicator.show_click_ring(x1, y1)
    pyautogui.dragTo(x2, y2, duration=0.25, button="left")


def open_app(name: str) -> bool:
    r = subprocess.run(["open", "-a", name], capture_output=True)
    try:
        from Cocoa import NSWorkspace, NSApplicationActivateIgnoringOtherApps
        ws = NSWorkspace.sharedWorkspace()
        for app in ws.runningApplications():
            n = (app.localizedName() or "").lower()
            bid = (app.bundleIdentifier() or "").lower()
            if n == name.lower() or name.lower() in bid or name.lower() in n:
                app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
                break
    except Exception:
        pass
    time.sleep(0.4)
    return r.returncode == 0


def quit_app(name: str | None = None):
    """Quit an application cleanly by name or active window with Command-Q."""
    if name:
        subprocess.run(["osascript", "-e", f'tell application "{name}" to quit'], capture_output=True)
    else:
        pyautogui.hotkey("command", "q")
    time.sleep(0.2)


def open_url(url: str):
    subprocess.run(["open", url])
    time.sleep(0.5)
