"""MCP server so Antigravity, Claude Code, or any MCP client can drive the Mac.

The calling agent does the thinking (no API key needed); these tools are its hands.
With a Gemini or Jev key in .env, `computer_task` also appears and runs a whole task in the harness."""

import difflib
import io
import os
from contextlib import redirect_stdout
from pathlib import Path

from .config import load_keys
from mcp.server.mcpserver import Image, MCPServer

from . import actions
from .screen import Element, ax_elements, screen_size, screenshot_png

load_keys()

INSTRUCTIONS = """Tools to operate the user's whole Mac desktop (any app, not just a browser).
Loop: take_screenshot and/or list_screen_elements -> one action -> check the result.
Use speak() to tell the user briefly what you're doing and what you found; they may be listening, not watching.
Prefer open_app / open_url / press_keys (keyboard shortcuts) over clicking. Prefer click_element
(accessibility element by id or description) over click_at (pixel coordinates); use click_at only
for apps with no useful elements. Ask the user before sending, buying, deleting or submitting anything."""

mcp = MCPServer("computer-use-lite", instructions=INSTRUCTIONS)
_last: dict = {"elements": [], "app": "", "shot_w": 0}


def _has(*names) -> bool:
    return any(os.environ.get(n) for n in names)


def _elements(refresh: bool = True) -> tuple[str, list[Element]]:
    if refresh or not _last["elements"]:
        _last["app"], _last["elements"] = ax_elements(limit=254)
    return _last["app"], _last["elements"]


def _match(description: str, elements: list[Element]) -> tuple[Element | None, str]:
    """Find an element by id ("e12") or description. Jev if a key is set, else fuzzy label match."""
    by_id = {e.id: e for e in elements}
    if description.strip() in by_id:
        return by_id[description.strip()], "id"
    if _has("TYPESAFE_API_KEY", "JEV_API_KEY"):
        from .jev import Jev
        el, p = Jev().ground({"kind": "click", "target": description, "do": description}, _last["app"], elements)
        if el is not None and p >= 0.5:
            return el, f"jev p={p:.2f}"
    want = description.lower()
    scored = [(difflib.SequenceMatcher(None, want, f"{e.label} {e.role[2:]}".lower()).ratio()
               + (0.3 if e.label and e.label.lower() in want else 0), e) for e in elements]
    score, best = max(scored, key=lambda t: t[0], default=(0, None))
    return (best, f"label match {score:.2f}") if score >= 0.45 else (None, "")


def _is_id(s: str) -> bool:
    return s.strip().startswith("e") and s.strip()[1:].isdigit()


def _narrate(text: str):
    """Speak a short update when the user has "Narrate while working" on (shared with the menu bar app)."""
    from .config import load_settings
    from . import say
    s = load_settings()
    if s.get("narrate"):
        say.speak(text, s.get("speaking_voice"), interrupt=False)


@mcp.tool()
def speak(text: str) -> str:
    """Say something out loud to the user in their chosen voice: progress, a question, or the result.
    Keep it to a sentence. Use it while working so the user hears what you're doing."""
    from .config import load_settings
    from . import say
    say.speak(text, load_settings().get("speaking_voice"))
    return "spoken"


@mcp.tool()
def take_screenshot() -> Image:
    """Screenshot of the main display. click_at uses this image's pixel coordinates."""
    from PIL import Image as PILImage

    png = screenshot_png()
    _last["shot_w"] = PILImage.open(io.BytesIO(png)).width
    return Image(data=png, format="png")


@mcp.tool()
def list_screen_elements() -> str:
    """Clickable/readable accessibility elements of the frontmost app, as 'id role "label" at (x,y)'."""
    app, els = _elements()
    return f"frontmost app: {app}\n" + "\n".join(f"{e.id} {e.describe()}" for e in els)


@mcp.tool()
def click_element(element: str, double: bool = False) -> str:
    """Click an element by id from list_screen_elements (e.g. "e12") or by description (e.g. "Send button")."""
    _, els = _elements(refresh=not _is_id(element))
    el, how = _match(element, els)
    if el is None and _has("TYPESAFE_API_KEY", "JEV_API_KEY"):
        try:
            from .jev import Jev
            pt, conf = Jev().locate(element)
            if pt and conf >= 0.2:
                cx, cy, _, _ = pt
                _narrate(f"Clicking {element}")
                actions.click(cx, cy, clicks=2 if double else 1)
                return f"clicked {element!r} via jev visual box at ({cx},{cy}) conf={conf:.2f}"
        except Exception:
            pass
    if el is None:
        return f"no element matches {element!r}; call list_screen_elements or use click_at"
    _narrate(f"Clicking {el.label}" if el.label else "Clicking")
    actions.click(el.x, el.y, clicks=2 if double else 1)
    return f"clicked {el.describe()} ({how})"


@mcp.tool()
def click_at(x: int, y: int, button: str = "left", double: bool = False) -> str:
    """Click at pixel coordinates of the latest take_screenshot image."""
    scale = screen_size()[0] / (_last["shot_w"] or screen_size()[0])
    actions.click(int(x * scale), int(y * scale), button=button, clicks=2 if double else 1)
    return f"clicked ({x},{y})"


@mcp.tool()
def type_text(text: str, element: str = "", press_enter: bool = False, clear_first: bool = False) -> str:
    """Type text into the focused field, or first click `element` (id or description) to focus it."""
    if element:
        msg = click_element(element)
        if msg.startswith("no element"):
            return msg
    _narrate("Typing " + " ".join(text.split()[:6]))
    actions.type_text(text, press_enter=press_enter, clear=clear_first)
    return "typed"


@mcp.tool()
def press_keys(keys: list[str]) -> str:
    """Press a key or shortcut, e.g. ["enter"], ["command", "t"], ["command", "shift", "4"]."""
    actions.hotkey(keys)
    return "pressed " + "+".join(keys)


@mcp.tool()
def scroll(direction: str = "down", element: str = "", amount: int = 400) -> str:
    """Scroll up/down/left/right over an element (id or description), or the screen centre."""
    w, h = screen_size()
    x, y = w // 2, h // 2
    if element:
        el, _ = _match(element, _elements(refresh=not _is_id(element))[1])
        if el is not None:
            x, y = el.x, el.y
    actions.scroll(x, y, direction, amount)
    return f"scrolled {direction}"


@mcp.tool()
def open_app(name: str) -> str:
    """Launch or bring forward a Mac app by name, e.g. "Notes", "Safari", "System Settings"."""
    _narrate(f"Opening {name}")
    return f"opened {name}" if actions.open_app(name) else f"no app named {name!r}"


@mcp.tool()
def open_url(url: str) -> str:
    """Open a URL in the default browser."""
    actions.open_url(url)
    return f"opened {url}"


@mcp.tool()
def usage_status() -> str:
    """How much of each model's quota this app has used, what's left, and when each resets."""
    from .config import load_settings
    from .usage import summary_lines
    return "\n".join(summary_lines(load_settings()["gemini_models"]))


if _has("GEMINI_API_KEY", "GOOGLE_API_KEY", "TYPESAFE_API_KEY", "JEV_API_KEY"):

    @mcp.tool()
    def computer_task(task: str, mode: str = "auto", allow_risky: bool = False, stay_awake: bool = True) -> str:
        """Hand a whole task to the harness's own loop (uses the keys in .env), instead of stepping yourself.
        mode: "auto" (best available), "hybrid" (Gemini plans, Jev clicks), "gemini" (Gemini vision only),
        "jev" (Jev only, cheapest; types only text that appears in the task).
        Risky steps (send/buy/delete) are refused unless allow_risky is true.
        stay_awake keeps the Mac from sleeping while the task runs. Returns a log of what happened."""
        from .agent import Agent, pick_mode
        from .awake import stay_awake as awake

        buf = io.StringIO()
        agent = Agent(mode=pick_mode(mode), allow_risky=allow_risky, confirm=lambda msg: allow_risky,
                      log=lambda s: print(s, file=buf))
        with redirect_stdout(buf), awake(stay_awake):
            try:
                agent.run(task)
            except Exception as e:
                print(f"error: {e!r}")
        return buf.getvalue()


def main():
    mcp.run()


if __name__ == "__main__":
    main()
