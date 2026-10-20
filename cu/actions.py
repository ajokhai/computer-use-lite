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
    from . import indicator
    from .screen import _load_native_lib
    indicator.show_cursor_active(x, y)
    lib = _load_native_lib()
    if lib and hasattr(lib, "cu_move_to"):
        try:
            lib.cu_move_to(float(x), float(y), float(duration * 1000.0))
            indicator.show_cursor_active(x, y)
            return
        except Exception:
            pass
    try:
        pyautogui.moveTo(x, y, duration=duration, tween=pyautogui.easeOutQuad)
    except Exception:
        pyautogui.moveTo(x, y)
    indicator.show_cursor_active(x, y)


def click(x, y, button="left", clicks=1):
    from . import indicator
    from .screen import _load_native_lib
    move_to(x, y, duration=0.10)
    indicator.show_click_ring(x, y)
    indicator.hide_cursor_active(delay=0.45)
    lib = _load_native_lib()
    if lib and hasattr(lib, "cu_click"):
        try:
            btn_code = 1 if button == "right" else 0
            lib.cu_click(float(x), float(y), btn_code, int(clicks))
            return
        except Exception:
            pass
    pyautogui.click(x, y, clicks=clicks, interval=0.04, button=button)


def paste():
    """Paste the clipboard content (press Command-V)."""
    hotkey(["command", "v"])
    time.sleep(0.08)


def copy_image_to_clipboard(path: str = "/tmp/cu_screenshot.png") -> bool:
    """Put image data onto the general pasteboard via Cocoa."""
    try:
        from Cocoa import NSPasteboard, NSImage, NSURL, NSPasteboardTypePNG, NSData
        url = NSURL.fileURLWithPath_(str(path))
        img = NSImage.alloc().initWithContentsOfURL_(url)
        if img:
            data = NSData.dataWithContentsOfURL_(url)
            pb = NSPasteboard.generalPasteboard()
            pb.clearContents()
            pb.writeObjects_([img])
            if data:
                pb.setData_forType_(data, NSPasteboardTypePNG)
                pb.setData_forType_(data, "public.png")
            time.sleep(0.06)
            return True
    except Exception:
        pass
    return False


def screenshot_to_clipboard(rect=None, path: str = "/tmp/cu_screenshot.png"):
    """Capture the screen or a region to disk, then put image data onto the clipboard."""
    from .screen import _load_native_lib
    lib = _load_native_lib()
    captured = False
    if lib and hasattr(lib, "cu_capture_to_file"):
        try:
            x, y, w, h = (float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])) if rect else (0.0, 0.0, 0.0, 0.0)
            captured = bool(lib.cu_capture_to_file(x, y, w, h, path.encode("utf-8")))
        except Exception:
            captured = False
    if not captured:
        cmd = ["screencapture", "-x"]
        if rect:
            x, y, w, h = rect
            cmd.append(f"-R{int(x)},{int(y)},{int(w)},{int(h)}")
        cmd.append(path)
        subprocess.run(cmd, check=True)
    copy_image_to_clipboard(path)
    time.sleep(0.08)


def paste_image(path: str = "/tmp/cu_screenshot.png"):
    """Ensure the image is freshly placed on the pasteboard, then paste."""
    copy_image_to_clipboard(path)
    hotkey(["command", "v"])
    time.sleep(0.15)


def type_text(text: str, press_enter=False, clear=False):
    if clear:
        hotkey(["command", "a"])
        hotkey(["backspace"])
    # Paste via the clipboard: fast, and handles non-ASCII that typewrite cannot.
    old = subprocess.run(["pbpaste"], capture_output=True).stdout
    subprocess.run(["pbcopy"], input=text.encode(), check=True)
    hotkey(["command", "v"])
    time.sleep(0.05)
    subprocess.run(["pbcopy"], input=old)
    if press_enter:
        hotkey(["return"])


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
    from .screen import _load_native_lib
    lib = _load_native_lib()
    if lib and hasattr(lib, "cu_hotkey"):
        try:
            csv_keys = ",".join(keys).encode("utf-8")
            lib.cu_hotkey(csv_keys)
            return
        except Exception:
            pass
    keys = [key_name(k) for k in keys]
    if len(keys) == 1:
        pyautogui.press(keys[0])
    else:
        pyautogui.hotkey(*keys)


def scroll(x, y, direction="down", amount=300):
    from . import indicator
    from .screen import _load_native_lib
    move_to(x, y, duration=0.08)
    indicator.show_click_ring(x, y)
    indicator.show_cursor_active(x, y)
    lines = max(1, int(amount / 10))
    try:
        lib = _load_native_lib()
        if lib and hasattr(lib, "cu_scroll"):
            try:
                dy = lines if direction == "up" else (-lines if direction == "down" else 0)
                dx = lines if direction == "right" else (-lines if direction == "left" else 0)
                lib.cu_scroll(dy, dx)
                return
            except Exception:
                pass
        if direction in ("up", "down"):
            pyautogui.scroll(lines if direction == "up" else -lines)
        else:
            pyautogui.hscroll(lines if direction == "right" else -lines)
    finally:
        indicator.hide_cursor_active(delay=0.45)


def drag(x1, y1, x2, y2):
    from . import indicator
    from .screen import _load_native_lib
    move_to(x1, y1, duration=0.08)
    indicator.show_click_ring(x1, y1)
    indicator.show_cursor_active(x1, y1)
    try:
        lib = _load_native_lib()
        if lib and hasattr(lib, "cu_drag"):
            try:
                lib.cu_drag(float(x1), float(y1), float(x2), float(y2), 250.0)
                indicator.show_cursor_active(x2, y2)
                return
            except Exception:
                pass
        pyautogui.dragTo(x2, y2, duration=0.25, button="left")
        indicator.show_cursor_active(x2, y2)
    finally:
        indicator.hide_cursor_active(delay=0.45)


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
