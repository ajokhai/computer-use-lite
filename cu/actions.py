"""Mouse and keyboard actions in screen points. Slam the mouse into a corner to abort (pyautogui failsafe)."""

import subprocess
import time

import pyautogui

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05

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


def click(x, y, button="left", clicks=1):
    pyautogui.click(x, y, clicks=clicks, interval=0.08, button=button)


def type_text(text: str, press_enter=False, clear=False):
    if clear:
        pyautogui.hotkey("command", "a")
        pyautogui.press("backspace")
    # Paste via the clipboard: fast, and handles non-ASCII that typewrite cannot.
    old = subprocess.run(["pbpaste"], capture_output=True).stdout
    subprocess.run(["pbcopy"], input=text.encode(), check=True)
    pyautogui.hotkey("command", "v")
    time.sleep(0.15)
    subprocess.run(["pbcopy"], input=old)
    if press_enter:
        pyautogui.press("enter")


def hotkey(keys):
    keys = [key_name(k) for k in keys]
    if len(keys) == 1:
        pyautogui.press(keys[0])
    else:
        pyautogui.hotkey(*keys)


def scroll(x, y, direction="down", amount=300):
    pyautogui.moveTo(x, y)
    # pyautogui scroll units are lines on macOS; ~10px each.
    lines = max(1, int(amount / 10))
    if direction in ("up", "down"):
        pyautogui.scroll(lines if direction == "up" else -lines)
    else:
        pyautogui.hscroll(lines if direction == "right" else -lines)


def drag(x1, y1, x2, y2):
    pyautogui.moveTo(x1, y1)
    pyautogui.dragTo(x2, y2, duration=0.4, button="left")


def open_app(name: str) -> bool:
    r = subprocess.run(["open", "-a", name], capture_output=True)
    time.sleep(1.2)
    return r.returncode == 0


def open_url(url: str):
    subprocess.run(["open", url])
    time.sleep(1.5)
