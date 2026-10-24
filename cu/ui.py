"""The app's widget look: rounded cards on a soft grey window, one big number per card with a small
caption, tinted capsule pills for status, warm gradient progress bars and capsule buttons.

Colours are dynamic (light and dark) wherever AppKit redraws them; the few layer colours that
can't follow the appearance (gradients, button fills) read well in both."""

from AppKit import (
    NSAppearanceNameAqua,
    NSAppearanceNameDarkAqua,
    NSAttributedString,
    NSBox,
    NSButton,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSMakeRect,
    NSTextField,
    NSView,
)
from Quartz import CAGradientLayer, CGColorCreateGenericRGB

RADIUS = 20.0
GAP = 16.0


def _rgb(hex_: str, alpha: float = 1.0) -> NSColor:
    v = int(hex_.lstrip("#"), 16)
    return NSColor.colorWithSRGBRed_green_blue_alpha_((v >> 16) / 255, (v >> 8 & 255) / 255, (v & 255) / 255, alpha)


def dynamic(light: NSColor, dark: NSColor) -> NSColor:
    """A colour that follows the window's appearance."""
    def pick(appearance):
        best = appearance.bestMatchFromAppearancesWithNames_([NSAppearanceNameAqua, NSAppearanceNameDarkAqua])
        return dark if best == NSAppearanceNameDarkAqua else light
    return NSColor.colorWithName_dynamicProvider_(None, pick)


BACKGROUND = dynamic(_rgb("#ECECEE"), _rgb("#141416"))
CARD = dynamic(_rgb("#FFFFFF"), _rgb("#1F1F22"))
INVERSE = dynamic(_rgb("#18181A"), _rgb("#34343A"))  # the highlighted card: near-black in light mode
INVERSE_RING = dynamic(_rgb("#18181A"), _rgb("#FFFFFF", 0.22))
INVERSE_TEXT = _rgb("#FFFFFF")
INVERSE_SECONDARY = _rgb("#FFFFFF", 0.6)
WARM = ("#F0562E", "#F6B23B")  # the gradient: orange to amber


def card(frame, fill: NSColor = CARD) -> NSBox:
    """A rounded card; add subviews to its contentView() (origin at the card's bottom left)."""
    box = NSBox.alloc().initWithFrame_(frame)
    box.setBoxType_(4)  # custom
    box.setTitlePosition_(0)  # no title
    box.setBorderWidth_(0)
    box.setCornerRadius_(RADIUS)
    box.setFillColor_(fill)
    box.setContentViewMargins_((0, 0))
    return box


def label(text: str, size: float, color=None, weight: float = 0.0, digits: bool = False, wrap: bool = False) -> NSTextField:
    f = NSTextField.wrappingLabelWithString_(text) if wrap else NSTextField.labelWithString_(text)
    font = (NSFont.monospacedDigitSystemFontOfSize_weight_ if digits else NSFont.systemFontOfSize_weight_)(size, weight)
    f.setFont_(font)
    f.setTextColor_(color or NSColor.labelColor())
    f.setSelectable_(False)
    return f


def place(parent, view, x: float, y: float, w: float | None = None, h: float | None = None):
    """Add `view` at (x, y) from the parent's bottom left, sized to fit unless w/h are given."""
    if w is None or h is None:
        view.sizeToFit()
        size = view.frame().size
        w, h = w or size.width, h or size.height
    view.setFrame_(NSMakeRect(x, y, w, h))
    parent.addSubview_(view)
    return view


def pill(text: str, tint: NSColor) -> NSBox:
    """A capsule with tinted text on a faint wash of the same colour (e.g. "Added", "~4H")."""
    t = label(text, 11, tint, 0.4)
    t.sizeToFit()
    w, h = t.frame().size.width + 18, 20.0
    box = card(NSMakeRect(0, 0, w, h), tint.colorWithAlphaComponent_(0.16))
    box.setCornerRadius_(h / 2)
    t.setFrame_(NSMakeRect(9, (h - t.frame().size.height) / 2, t.frame().size.width, t.frame().size.height))
    box.contentView().addSubview_(t)
    return box


def gradient_bar(frame, fraction: float) -> NSView:
    """A thin track with a warm gradient filled to `fraction` (0–1)."""
    w, h = frame.size.width, frame.size.height
    view = NSView.alloc().initWithFrame_(frame)
    view.setWantsLayer_(True)
    track = view.layer()
    track.setBackgroundColor_(CGColorCreateGenericRGB(0.5, 0.5, 0.5, 0.18))
    track.setCornerRadius_(h / 2)
    fill = CAGradientLayer.layer()
    fill.setColors_([_rgb(c).CGColor() for c in WARM])
    fill.setStartPoint_((0, 0.5))
    fill.setEndPoint_((1, 0.5))
    fill.setCornerRadius_(h / 2)
    fill.setFrame_(((0, 0), (max(h, w * max(0.0, min(1.0, fraction))), h)))
    track.addSublayer_(fill)
    return view


def capsule_button(title: str, target, action: str, primary: bool = False, on_dark: bool = False) -> NSButton:
    """A borderless capsule: filled with the accent colour when primary, a soft grey wash otherwise."""
    b = NSButton.buttonWithTitle_target_action_(title, target, action)
    b.setBordered_(False)
    b.setWantsLayer_(True)
    b.setFont_(NSFont.systemFontOfSize_weight_(13, 0.3))
    b.sizeToFit()
    w, h = max(b.frame().size.width + 28, 76), 30.0
    b.setFrame_(NSMakeRect(0, 0, w, h))
    layer = b.layer()
    layer.setCornerRadius_(h / 2)
    if primary:
        layer.setBackgroundColor_(NSColor.controlAccentColor().CGColor())
        color = INVERSE_TEXT
    elif on_dark:
        layer.setBackgroundColor_(CGColorCreateGenericRGB(1, 1, 1, 0.14))
        color = INVERSE_TEXT
    else:
        layer.setBackgroundColor_(CGColorCreateGenericRGB(0.5, 0.5, 0.5, 0.16))
        color = NSColor.labelColor()
    b.setContentTintColor_(color)
    b.setAttributedTitle_(NSAttributedString.alloc().initWithString_attributes_(
        title, {NSForegroundColorAttributeName: color, NSFontAttributeName: b.font()}))
    return b



def window(title: str, width: float, height: float, closable: bool = True):
    """A panel in the app's look: transparent title bar, soft grey background, content to the top edge."""
    from AppKit import (NSPanel, NSWindowStyleMaskClosable, NSWindowStyleMaskFullSizeContentView,
                        NSWindowStyleMaskTitled)
    mask = NSWindowStyleMaskTitled | NSWindowStyleMaskFullSizeContentView | (NSWindowStyleMaskClosable if closable else 0)
    panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(NSMakeRect(0, 0, width, height), mask, 2, False)
    panel.setTitle_(title)  # for the Window menu and accessibility; hidden in the bar
    panel.setTitlebarAppearsTransparent_(True)
    panel.setTitleVisibility_(1)
    panel.setBackgroundColor_(BACKGROUND)
    panel.setReleasedWhenClosed_(False)
    return panel


def header(content, title: str, intro: str, pad: float, width: float, top: float) -> float:
    """The big bold title and its grey intro under the title bar; returns the y below them."""
    place(content, label(title, 24, weight=0.4), pad, top - 72)
    if not intro:
        return top - 84
    lines = label(intro, 13, NSColor.secondaryLabelColor(), wrap=True)
    place(content, lines, pad, top - 116, width - 2 * pad, 36)
    return top - 132


def retitle(button: NSButton, title: str, keep_right: bool = True):
    """Change a capsule button's title and width, keeping its right edge (or left) in place."""
    color = button.attributedTitle().attribute_atIndex_effectiveRange_(NSForegroundColorAttributeName, 0, None)[0] \
        if button.attributedTitle().length() else NSColor.labelColor()
    button.setAttributedTitle_(NSAttributedString.alloc().initWithString_attributes_(
        title, {NSForegroundColorAttributeName: color, NSFontAttributeName: button.font()}))
    f = button.frame()
    probe = NSButton.buttonWithTitle_target_action_(title, None, None)
    probe.setBordered_(False)
    probe.setFont_(button.font())
    probe.sizeToFit()
    w = max(probe.frame().size.width + 28, 76)
    x = f.origin.x + f.size.width - w if keep_right else f.origin.x
    button.setFrame_(NSMakeRect(x, f.origin.y, w, f.size.height))


def enable(button: NSButton, on: bool):
    button.setEnabled_(on)
    button.setAlphaValue_(1.0 if on else 0.4)


def swap_pill(parent, old, text: str, tint: NSColor, right: float, y: float):
    """Replace a pill in place (right-aligned at `right`); returns the new one."""
    if old is not None:
        old.removeFromSuperview()
    p = pill(text, tint)
    w = p.frame().size.width
    return place(parent, p, right - w, y, w, p.frame().size.height)
