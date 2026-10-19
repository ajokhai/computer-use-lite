"""Draw the app icon (AppIcon.icns) and the DMG window background. Run: uv run python packaging/make_art.py"""

import math
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).parent
TOP, BOTTOM = (58, 66, 120), (28, 30, 58)  # indigo gradient
RAY = (125, 200, 255)


def gradient(w, h, top, bottom):
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / (h - 1)
        d.line([(0, y), (w, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
    return img


def cursor_points(tip, scale):
    # Classic arrow pointer, tip at the origin.
    shape = [(0, 0), (0, 15.5), (3.6, 12.1), (6.3, 18.2), (8.6, 17.2), (6.0, 11.3), (11.0, 11.0)]
    return [(tip[0] + x * scale, tip[1] + y * scale) for x, y in shape]


def draw_mark(img, tip, scale):
    """Cursor with click rays around its tip, drawn onto img (RGBA)."""
    w, h = img.size
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).polygon(cursor_points((tip[0] + scale * 0.6, tip[1] + scale * 1.0), scale),
                                   fill=(0, 0, 0, 120))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(scale * 0.8)))

    d = ImageDraw.Draw(img)
    for angle in (-170, -135, -100, -65):  # rays fanned around the upper-left of the tip
        a = math.radians(angle)
        r1, r2 = scale * 3.2, scale * 6.4
        p1 = (tip[0] + r1 * math.cos(a), tip[1] + r1 * math.sin(a))
        p2 = (tip[0] + r2 * math.cos(a), tip[1] + r2 * math.sin(a))
        d.line([p1, p2], fill=RAY, width=int(scale * 1.25))
        for p in (p1, p2):  # round caps
            rr = scale * 0.62
            d.ellipse([p[0] - rr, p[1] - rr, p[0] + rr, p[1] + rr], fill=RAY)
    pts = cursor_points(tip, scale)
    d.polygon(pts, fill="white")
    d.line(pts + [pts[0]], fill=(225, 230, 245), width=max(1, int(scale * 0.35)), joint="curve")


def icon(size=1024, ss=4):
    S = size * ss
    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    inset, radius = int(S * 0.098), int(S * 0.185)  # macOS Big Sur icon grid
    tile = gradient(S, S, TOP, BOTTOM).convert("RGBA")
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([inset, inset, S - inset, S - inset], radius=radius, fill=255)
    drop = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(drop).rounded_rectangle([inset, inset + S * 0.012, S - inset, S - inset + S * 0.012],
                                           radius=radius, fill=(0, 0, 0, 90))
    canvas.alpha_composite(drop.filter(ImageFilter.GaussianBlur(S * 0.012)))
    canvas.paste(tile, (0, 0), mask)
    draw_mark(canvas, (S * 0.43, S * 0.33), S * 0.026)
    return canvas.resize((size, size), Image.LANCZOS)


def write_icns(img, out: Path):
    iconset = HERE / "AppIcon.iconset"
    iconset.mkdir(exist_ok=True)
    for s in (16, 32, 128, 256, 512):
        img.resize((s, s), Image.LANCZOS).save(iconset / f"icon_{s}x{s}.png")
        img.resize((s * 2, s * 2), Image.LANCZOS).save(iconset / f"icon_{s}x{s}@2x.png")
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True)


def font(size, bold=False):
    for path in ("/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/Helvetica.ttc"):
        try:
            f = ImageFont.truetype(path, size)
            if bold:
                try:
                    f.set_variation_by_name("Semibold")
                except Exception:
                    pass
            return f
        except OSError:
            continue
    return ImageFont.load_default()


def dmg_background(w=660, h=400, ss=2):
    """Window background: soft gradient, title, and an arrow from the app slot to Applications."""
    W, H = w * ss, h * ss
    img = gradient(W, H, (246, 247, 252), (228, 231, 243)).convert("RGBA")
    d = ImageDraw.Draw(img)
    d.text((W / 2, 52 * ss), "Computer Use", font=font(26 * ss, bold=True), fill=(30, 32, 60), anchor="mm")
    d.text((W / 2, 84 * ss), "Drag to Applications to install", font=font(14 * ss), fill=(110, 114, 140), anchor="mm")
    # Arrow between the two icon slots (icons sit at x=170 and x=490, y=215).
    y, x1, x2 = 215 * ss, 250 * ss, 410 * ss
    col = (120, 126, 170)
    d.line([(x1, y), (x2 - 14 * ss, y)], fill=col, width=4 * ss)
    d.polygon([(x2, y), (x2 - 20 * ss, y - 12 * ss), (x2 - 20 * ss, y + 12 * ss)], fill=col)
    d.text((W / 2, 352 * ss), "First launch: Settings › Grant Accessibility and Screen Recording",
           font=font(11 * ss), fill=(130, 134, 160), anchor="mm")
    img = img.convert("RGB")
    img.resize((w, h), Image.LANCZOS).save(HERE / "dmg-background.png")
    img.save(HERE / "dmg-background@2x.png")


if __name__ == "__main__":
    art = icon()
    art.save(HERE / "AppIcon.png")
    write_icns(art, HERE / "AppIcon.icns")
    dmg_background()
    print("wrote AppIcon.icns, AppIcon.png, dmg-background.png")
