"""On-screen visual indicator: pulses a glowing cyan ring around target coordinates
when the agent clicks, so the user can easily follow what the agent is doing."""

import time
from Foundation import NSOperationQueue, NSTimer
from AppKit import (NSApplication, NSBezierPath, NSColor, NSFloatingWindowLevel,
                    NSMakeRect, NSPanel, NSScreen, NSView, NSWindowStyleMaskBorderless)

_ACTIVE_PULSES = set()


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
        self.panel.setLevel_(NSFloatingWindowLevel + 100)
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
