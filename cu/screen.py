"""macOS screen state: screenshots and the accessibility (AX) tree of the frontmost app."""

import io
import subprocess
import tempfile
import time
from dataclasses import dataclass

import pyautogui
from AppKit import NSWorkspace
from ApplicationServices import (
    AXUIElementCopyAttributeValue,
    AXUIElementCreateApplication,
    AXValueGetValue,
    kAXValueCGPointType,
    kAXValueCGSizeType,
)
from PIL import Image

# Roles worth acting on. Everything else is only walked through, not listed.
ACTIONABLE = {
    "AXButton", "AXTextField", "AXTextArea", "AXSearchField", "AXComboBox",
    "AXCheckBox", "AXRadioButton", "AXPopUpButton", "AXMenuButton", "AXLink",
    "AXMenuItem", "AXMenuBarItem", "AXTab", "AXSlider", "AXCell", "AXRow",
    "AXDisclosureTriangle", "AXIncrementor", "AXImage", "AXStaticText",
}
TEXT_INPUT = {"AXTextField", "AXTextArea", "AXSearchField", "AXComboBox"}


@dataclass
class Element:
    id: str
    role: str
    label: str
    x: int  # centre, in screen points
    y: int
    w: int
    h: int
    focused: bool = False

    def describe(self) -> str:
        role = self.role.removeprefix("AX")
        flag = " [focused]" if self.focused else ""
        return f'{role} "{self.label}"{flag} at ({self.x},{self.y})'


def screen_size() -> tuple[int, int]:
    """Main display size in points (the space pyautogui clicks in)."""
    w, h = pyautogui.size()
    return int(w), int(h)


def screenshot_png(max_width: int = 1440) -> bytes:
    """Capture the main display, downscaled from Retina pixels so tokens stay low."""
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        lib = _load_native_lib()
        captured = False
        if lib and hasattr(lib, "cu_capture_to_file"):
            try:
                captured = bool(lib.cu_capture_to_file(0.0, 0.0, 0.0, 0.0, f.name.encode("utf-8")))
            except Exception:
                captured = False
        if not captured:
            subprocess.run(["screencapture", "-x", "-C", "-m", f.name], check=True)
        img = Image.open(f.name)
        img.load()
    pw, _ = screen_size()
    target = min(max_width, pw)
    if img.width != target:
        img = img.resize((target, round(img.height * target / img.width)), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def frontmost_app() -> tuple[str, int]:
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    return str(app.localizedName()), int(app.processIdentifier())


def _attr(el, name):
    err, val = AXUIElementCopyAttributeValue(el, name, None)
    return val if err == 0 else None


def _point(val, kind):
    if val is None:
        return None
    ok, out = AXValueGetValue(val, kind, None)
    return out if ok else None


def _label(el) -> str:
    for name in ("AXTitle", "AXDescription", "AXValue", "AXPlaceholderValue", "AXHelp"):
        v = _attr(el, name)
        if isinstance(v, str) and v.strip():
            return " ".join(v.split())[:80]
    return ""


def is_on_screen(x: int, y: int) -> bool:
    """Check if (x, y) is within any connected screen (supports multi-monitor)."""
    from AppKit import NSScreen
    screens = NSScreen.screens()
    if not screens:
        sw, sh = screen_size()
        return 0 <= x < sw and 0 <= y < sh
    primary_h = screens[0].frame().size.height
    for s in screens:
        f = s.frame()
        ax_x = f.origin.x
        ax_y = primary_h - (f.origin.y + f.size.height)
        if ax_x <= x < ax_x + f.size.width and ax_y <= y < ax_y + f.size.height:
            return True
    return False


_NATIVE_LIB = None

def _load_native_lib():
    global _NATIVE_LIB
    if _NATIVE_LIB is not None:
        return _NATIVE_LIB
    import ctypes
    import sys
    from pathlib import Path
    candidates = [
        Path(__file__).resolve().parent.parent / "crates" / "cu-native" / "target" / "release" / "libcu_native.dylib",
    ]
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidates.insert(0, Path(sys._MEIPASS) / "libcu_native.dylib")

    for candidate in candidates:
        if candidate.is_file():
            try:
                lib = ctypes.CDLL(str(candidate))
                lib.cu_walk_ax_tree_json.argtypes = [ctypes.c_size_t]
                lib.cu_walk_ax_tree_json.restype = ctypes.c_char_p
                lib.cu_free_string.argtypes = [ctypes.c_char_p]
                lib.cu_free_string.restype = None

                if hasattr(lib, "cu_click"):
                    lib.cu_click.argtypes = [ctypes.c_double, ctypes.c_double, ctypes.c_uint32, ctypes.c_size_t]
                    lib.cu_click.restype = None
                if hasattr(lib, "cu_move_to"):
                    lib.cu_move_to.argtypes = [ctypes.c_double, ctypes.c_double, ctypes.c_double]
                    lib.cu_move_to.restype = None
                if hasattr(lib, "cu_mouse_pos"):
                    lib.cu_mouse_pos.argtypes = [ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_double)]
                    lib.cu_mouse_pos.restype = None
                if hasattr(lib, "cu_key_press"):
                    lib.cu_key_press.argtypes = [ctypes.c_uint16, ctypes.c_uint64]
                    lib.cu_key_press.restype = None
                if hasattr(lib, "cu_hotkey"):
                    lib.cu_hotkey.argtypes = [ctypes.c_char_p]
                    lib.cu_hotkey.restype = None
                if hasattr(lib, "cu_scroll"):
                    lib.cu_scroll.argtypes = [ctypes.c_int32, ctypes.c_int32]
                    lib.cu_scroll.restype = None
                if hasattr(lib, "cu_drag"):
                    lib.cu_drag.argtypes = [ctypes.c_double, ctypes.c_double, ctypes.c_double, ctypes.c_double, ctypes.c_double]
                    lib.cu_drag.restype = None
                if hasattr(lib, "cu_capture_to_file"):
                    lib.cu_capture_to_file.argtypes = [ctypes.c_double, ctypes.c_double, ctypes.c_double, ctypes.c_double, ctypes.c_char_p]
                    lib.cu_capture_to_file.restype = ctypes.c_bool
                if hasattr(lib, "cu_memory_find_match"):
                    lib.cu_memory_find_match.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
                    lib.cu_memory_find_match.restype = ctypes.c_char_p
                if hasattr(lib, "cu_memory_save_pattern"):
                    lib.cu_memory_save_pattern.argtypes = [ctypes.c_char_p]
                    lib.cu_memory_save_pattern.restype = ctypes.c_bool
                if hasattr(lib, "cu_memory_list_patterns"):
                    lib.cu_memory_list_patterns.argtypes = []
                    lib.cu_memory_list_patterns.restype = ctypes.c_char_p
                if hasattr(lib, "cu_memory_free_string"):
                    lib.cu_memory_free_string.argtypes = [ctypes.c_char_p]
                    lib.cu_memory_free_string.restype = None
                if hasattr(lib, "cu_check_typing_safety"):
                    lib.cu_check_typing_safety.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p]
                    lib.cu_check_typing_safety.restype = ctypes.c_char_p
                if hasattr(lib, "cu_check_doc_safety"):
                    lib.cu_check_doc_safety.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p]
                    lib.cu_check_doc_safety.restype = ctypes.c_char_p
                if hasattr(lib, "cu_is_risky_action"):
                    lib.cu_is_risky_action.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p]
                    lib.cu_is_risky_action.restype = ctypes.c_bool
                if hasattr(lib, "cu_verify_task_completion"):
                    lib.cu_verify_task_completion.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p]
                    lib.cu_verify_task_completion.restype = ctypes.c_char_p
                if hasattr(lib, "cu_evaluate_agent_stuck"):
                    lib.cu_evaluate_agent_stuck.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p]
                    lib.cu_evaluate_agent_stuck.restype = ctypes.c_char_p
                if hasattr(lib, "cu_extract_text_candidates"):
                    lib.cu_extract_text_candidates.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
                    lib.cu_extract_text_candidates.restype = ctypes.c_char_p

                _NATIVE_LIB = lib
                return _NATIVE_LIB
            except Exception:
                pass
    return None


def ax_elements(limit: int = 200, timeout: float = 2.5) -> tuple[str, list[Element]]:
    """Walk the frontmost app's AX tree and return on-screen actionable elements."""
    lib = _load_native_lib()
    if lib:
        try:
            import json
            raw = lib.cu_walk_ax_tree_json(limit)
            if raw:
                data = json.loads(raw.decode("utf-8"))
                app_name = data.get("app", "Unknown")
                elements = [
                    Element(
                        id=e["id"],
                        role=e["role"],
                        label=e["label"],
                        x=e["x"],
                        y=e["y"],
                        w=e["w"],
                        h=e["h"],
                        focused=e.get("focused", False),
                    )
                    for e in data.get("elements", [])
                ]
                return app_name, elements
        except Exception:
            pass

    app_name, pid = frontmost_app()
    root = AXUIElementCreateApplication(pid)
    focused = _attr(root, "AXFocusedUIElement")
    out: list[Element] = []
    seen: set[tuple] = set()
    deadline = time.monotonic() + timeout

    def visit(el, depth):
        if len(out) >= limit or depth > 40 or time.monotonic() > deadline:
            return
        role = _attr(el, "AXRole") or ""
        if role in ACTIONABLE:
            pos = _point(_attr(el, "AXPosition"), kAXValueCGPointType)
            size = _point(_attr(el, "AXSize"), kAXValueCGSizeType)
            label = _label(el)
            if pos and size and size.width > 2 and size.height > 2:
                cx, cy = int(pos.x + size.width / 2), int(pos.y + size.height / 2)
                on_screen = is_on_screen(cx, cy)
                key = (role, label, cx, cy)
                # Static text and images are only useful when they say something.
                useful = role not in ("AXStaticText", "AXImage") or label
                if on_screen and useful and key not in seen:
                    seen.add(key)
                    out.append(Element(
                        id=f"e{len(out)}", role=role, label=label, x=cx, y=cy,
                        w=int(size.width), h=int(size.height),
                        focused=focused is not None and el == focused,
                    ))
        children = _attr(el, "AXChildren") or []
        # Prune long lists/tables so sidebar note lists don't starve main editor panes
        if role in ("AXOutline", "AXTable", "AXList") and len(children) > 12:
            children = children[:12]
        for child in children:
            visit(child, depth + 1)

    windows = list(_attr(root, "AXWindows") or [])
    if not windows:
        for w in (_attr(root, "AXMainWindow"), _attr(root, "AXFocusedWindow")):
            if w and w not in windows:
                windows.append(w)
    for window in windows:
        visit(window, 0)
    menubar = _attr(root, "AXMenuBar")
    if menubar is not None:
        for item in _attr(menubar, "AXChildren") or []:
            visit(item, 0)
    return app_name, out


def ax_trusted() -> bool:
    from ApplicationServices import AXIsProcessTrusted
    return bool(AXIsProcessTrusted())
