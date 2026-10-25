"""CLI: uv run python -m cu "task"   |   --doctor   |   --elements"""

import argparse
import sys

from cu.config import load_keys


def main():
    load_keys()
    p = argparse.ArgumentParser(prog="cu", description="Computer use for the whole Mac")
    p.add_argument("task", nargs="*")
    p.add_argument("--mode", choices=["auto", "hybrid", "jev", "vision", "gemini"], default="auto",
                   help="auto: best your keys allow. hybrid: a vision model plans, Jev acts. "
                        "jev: Jev only (cheapest). vision: a vision model does everything (gemini: old name).")
    p.add_argument("--confirm", action="store_true", help="ask before every action")
    p.add_argument("--yolo", action="store_true", help="don't ask before risky steps (send/buy/delete)")
    p.add_argument("--awake", action="store_true", help="keep the Mac awake while the task runs")
    p.add_argument("--doctor", action="store_true", help="check permissions and keys")
    p.add_argument("--elements", action="store_true", help="print the frontmost app's AX elements")
    a = p.parse_args()

    if a.doctor:
        return doctor()
    if a.elements:
        from .screen import ax_elements
        app, els = ax_elements()
        print(f"{app}: {len(els)} elements")
        for e in els:
            print(f"  {e.id:>5} {e.describe()}")
        return
    if not a.task:
        p.error("give a task, e.g. cu \"open Notes and write a shopping list\"")

    from .agent import Agent
    from .awake import stay_awake
    agent = Agent(mode=a.mode, confirm_all=a.confirm, allow_risky=a.yolo)
    print("Abort any time: slam the mouse into a screen corner, or Ctrl+C.\n")
    try:
        with stay_awake(a.awake):
            agent.run(" ".join(a.task))
    except KeyboardInterrupt:
        sys.exit("\naborted")
    except RuntimeError as e:  # a setup problem (e.g. a text-only model): one line, not a traceback
        sys.exit(f"stopped: {e}")


def doctor():
    import os
    from .screen import ax_trusted, screenshot_png, screen_size
    ok = lambda b: "✓" if b else "✗"
    print(f"{ok(ax_trusted())} Accessibility permission (System Settings › Privacy › Accessibility → your terminal/VS Code)")
    try:
        png = screenshot_png()
        print(f"✓ Screen capture works ({len(png)//1024} KB; check it isn't just wallpaper → Screen Recording permission)")
    except Exception as e:
        print(f"✗ Screen capture: {e}")
    print(f"  screen: {screen_size()} points")
    print(f"{ok(os.environ.get('GEMINI_API_KEY') or os.environ.get('GOOGLE_API_KEY'))} GEMINI_API_KEY (optional)")
    print(f"{ok(os.environ.get('TYPESAFE_API_KEY') or os.environ.get('JEV_API_KEY'))} TYPESAFE_API_KEY (optional, Jev)")
    from .agent import pick_mode
    try:
        print(f"  standalone mode: {pick_mode()}")
    except RuntimeError:
        print("  no keys: drive the Mac from Antigravity or Claude via the MCP tools (no key needed)")


if __name__ == "__main__":
    main()
