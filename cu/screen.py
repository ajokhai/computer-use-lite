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


def ax_elements(limit: int = 200, timeout: float = 2.5) -> tuple[str, list[Element]]:
    """Walk the frontmost app's AX tree and return on-screen actionable elements."""
    app_name, pid = frontmost_app()
    root = AXUIElementCreateApplication(pid)
    sw, sh = screen_size()
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
                on_screen = 0 <= cx < sw and 0 <= cy < sh
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
        for child in _attr(el, "AXChildren") or []:
            visit(child, depth + 1)

    for window in _attr(root, "AXWindows") or []:
        visit(window, 0)
    menubar = _attr(root, "AXMenuBar")
    if menubar is not None:
        for item in _attr(menubar, "AXChildren") or []:
            visit(item, 0)
    return app_name, out


def ax_trusted() -> bool:
    from ApplicationServices import AXIsProcessTrusted
    return bool(AXIsProcessTrusted())
