"""Support links for the menu: the tip page and the button label Josh set there ("Buy me a Beer"),
read from the page so the menu matches whatever it says. "Buy me a drink" when it can't be read."""

import html
import re
import urllib.request

URL = "https://buymeamalt.com/@ajokhai"
REPO = "https://github.com/ajokhai/computer-use-lite"
DEFAULT = "Buy me a drink"


def label(timeout: float = 10) -> str:
    try:
        req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0 (Computer Use)"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            page = r.read().decode("utf-8", "replace")
    except OSError:
        return DEFAULT
    m = re.search(r'support-button[^>]*>(.*?)</button>', page, re.S)
    if not m:
        return DEFAULT
    # the first run of text in the button is the label; the price follows in its own span
    first = next((t.strip() for t in re.split(r"<[^>]+>", m.group(1)) if t.strip()), "")
    first = html.unescape(first)
    return first if 0 < len(first) < 60 else DEFAULT
