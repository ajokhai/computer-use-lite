"""On-screen visual indicator:
1. Live cursor beacon: shows a sleek glowing cyan halo and '● AI' badge directly at the cursor
   whenever Computer Use is actively steering or holding control of the cursor.
2. Click ring: pulses a vibrant cyan ripple around target coordinates on click.
"""

import threading
import time
from Foundation import NSOperationQueue, NSTimer, NSPoint
from AppKit import (
    NSApplication,
    NSAttributedString,
    NSBezierPath,
    NSColor,
    NSFloatingWindowLevel,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSMakeRect,
    NSMakePoint,
    NSPanel,
    NSScreen,
    NSView,
    NSWindowStyleMaskBorderless,
)

_ACTIVE_PULSES = set()
_BEACON = None
_BEACON_LOCK = threading.Lock()


class CursorBeaconView(NSView):
    def drawRect_(self, dirtyRect):
        NSColor.clearColor().set()
        NSBezierPath.fillRect_(dirtyRect)

        bounds = self.bounds()
        center_x = 22.0
        center_y = bounds.size.height - 22.0

        # Glowing cyan cursor halo (surrounding the system mouse pointer)
        glow_radius = 16.0
        glow_rect = NSMakeRect(center_x - glow_radius, center_y - glow_radius, glow_radius * 2, glow_radius * 2)
        glow_path = NSBezierPath.bezierPathWithOvalInRect_(glow_rect)
        glow_path.setLineWidth_(2.0)
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.0, 0.82, 1.0, 0.85).set()
        glow_path.stroke()

        # Inner subtle aura
        inner_rect = NSMakeRect(center_x - 8.0, center_y - 8.0, 16.0, 16.0)
        inner_path = NSBezierPath.bezierPathWithOvalInRect_(inner_rect)
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.0, 0.82, 1.0, 0.22).set()
        inner_path.fill()

        # Center tracking dot
        dot = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(center_x - 3.0, center_y - 3.0, 6.0, 6.0))
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.0, 0.95, 1.0, 0.95).set()
        dot.fill()

        # High-tech pill badge next to cursor: "● AI"
        badge_rect = NSMakeRect(center_x + 16.0, center_y - 9.0, 42.0, 18.0)
        badge_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(badge_rect, 9.0, 9.0)
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.10, 0.11, 0.13, 0.92).set()
        badge_path.fill()

        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.0, 0.82, 1.0, 0.50).set()
        badge_path.setLineWidth_(1.0)
        badge_path.stroke()

        # Pill text
        attrs = {
            NSFontAttributeName: NSFont.boldSystemFontOfSize_(10),
            NSForegroundColorAttributeName: NSColor.colorWithCalibratedRed_green_blue_alpha_(0.0, 0.92, 1.0, 1.0),
        }
        astr = NSAttributedString.alloc().initWithString_attributes_("● AI", attrs)
        astr.drawAtPoint_(NSMakePoint(center_x + 22.0, center_y - 7.0))


class CursorBeacon:
    def __init__(self):
        self.width = 90.0
        self.height = 44.0
        self.panel = None
        self.view = None
        self.hide_timer = None
        self._setup()

    def _setup(self):
        rect = NSMakeRect(-1000, -1000, self.width, self.height)
        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, NSWindowStyleMaskBorderless, 2, False
        )
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(NSColor.clearColor())
        self.panel.setIgnoresMouseEvents_(True)
        self.panel.setLevel_(NSFloatingWindowLevel + 105)
        self.panel.setHasShadow_(False)

        self.view = CursorBeaconView.alloc().initWithFrame_(NSMakeRect(0, 0, self.width, self.height))
        self.panel.setContentView_(self.view)

    def move_to(self, x: float, y: float):
        if self.hide_timer:
            self.hide_timer.invalidate()
            self.hide_timer = None

        screens = NSScreen.screens()
        primary_h = screens[0].frame().size.height if screens else 1080.0
        # Position panel so (center_x, center_y) lines up with cursor (x, y)
        origin_x = x - 22.0
        origin_y = primary_h - y - (self.height - 22.0)

        if self.panel:
            self.panel.setFrameOrigin_(NSPoint(origin_x, origin_y))
            if not self.panel.isVisible():
                self.panel.orderFrontRegardless()

    def schedule_hide(self, delay: float = 0.4):
        if self.hide_timer:
            self.hide_timer.invalidate()
        self.hide_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            delay, self, "onHideTimer:", None, False
        )

    def onHideTimer_(self, timer):
        if self.panel and self.panel.isVisible():
            self.panel.orderOut_(None)
        self.hide_timer = None

    def close(self):
        if self.hide_timer:
            self.hide_timer.invalidate()
            self.hide_timer = None
        if self.panel:
            self.panel.close()
            self.panel = None


def get_cursor_beacon() -> CursorBeacon:
    global _BEACON
    if _BEACON is None:
        _BEACON = CursorBeacon()
    return _BEACON


def show_cursor_active(x: float, y: float):
    """Show the live cursor beacon at (x, y) to indicate Computer Use is using the cursor."""
    def _do():
        try:
            beacon = get_cursor_beacon()
            beacon.move_to(float(x), float(y))
        except Exception:
            pass

    try:
        NSOperationQueue.mainQueue().addOperationWithBlock_(_do)
    except Exception:
        pass


def hide_cursor_active(delay: float = 0.4):
    """Schedule hiding the cursor beacon after Computer Use yields the cursor."""
    def _do():
        try:
            beacon = get_cursor_beacon()
            beacon.schedule_hide(delay=delay)
        except Exception:
            pass

    try:
        NSOperationQueue.mainQueue().addOperationWithBlock_(_do)
    except Exception:
        pass


class PulseView(NSView):
    def drawRect_(self, dirtyRect):
        NSColor.clearColor().set()
        NSBezierPath.fillRect_(dirtyRect)
        pw = getattr(self, "pw", None)
        if not pw:
            return
        progress = getattr(pw, "progress", 0.0)

        # Outer expanding ring (from 35% of size up to 100%)
        r_size = pw.size * (0.35 + 0.65 * progress)
        alpha = max(0.0, 0.9 * (1.0 - progress))
        inset = (pw.size - r_size) / 2.0

        path = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(inset, inset, r_size, r_size))
        path.setLineWidth_(2.5)
        # Vibrant cyan/electric blue glow
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.0, 0.82, 1.0, alpha).set()
        path.stroke()

        # Center focus dot
        dot_alpha = max(0.0, 0.95 * (1.0 - progress * 1.5))
        if dot_alpha > 0:
            dot = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(pw.size / 2 - 3, pw.size / 2 - 3, 6, 6))
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.0, 0.82, 1.0, dot_alpha).set()
            dot.fill()


class PulseWindow:
    def __init__(self, x: float, y: float, size: int = 64, duration: float = 0.35):
        self.size = size
        self.duration = duration
        self.progress = 0.0
        self.start_time = time.time()

        screens = NSScreen.screens()
        primary_h = screens[0].frame().size.height if screens else 1080.0
        # macOS coordinates: (0, 0) is bottom-left of primary screen
        rect = NSMakeRect(x - size / 2.0, primary_h - y - size / 2.0, size, size)

        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, NSWindowStyleMaskBorderless, 2, False
        )
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(NSColor.clearColor())
        self.panel.setIgnoresMouseEvents_(True)
        self.panel.setLevel_(NSFloatingWindowLevel + 110)
        self.panel.setHasShadow_(False)

        self.view = PulseView.alloc().initWithFrame_(NSMakeRect(0, 0, size, size))
        self.view.pw = self
        self.panel.setContentView_(self.view)
        self.panel.orderFrontRegardless()

        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.03, self, "tick:", None, True
        )
        _ACTIVE_PULSES.add(self)

    def tick_(self, timer):
        elapsed = time.time() - self.start_time
        if elapsed >= self.duration:
            timer.invalidate()
            if self.panel:
                self.panel.close()
                self.panel = None
            _ACTIVE_PULSES.discard(self)
        else:
            self.progress = elapsed / self.duration
            if self.view:
                self.view.setNeedsDisplay_(True)


def show_click_ring(x: int, y: int, size: int = 64, duration: float = 0.35):
    """Thread-safe trigger for on-screen click ring animation."""
    def _create():
        try:
            PulseWindow(x, y, size=size, duration=duration)
        except Exception:
            pass

    try:
        NSOperationQueue.mainQueue().addOperationWithBlock_(_create)
    except Exception:
        pass
