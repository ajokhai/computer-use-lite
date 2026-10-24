"""Entry point for the packaged app: menu bar by default, `--cli` for the CLI, `--speak TEXT`
to say something in the user's voice (used by the MCP server), and `--mcp`, which hands over
to the native MCP server for editor configs written by older versions."""

import os
import sys


def main():
    if "--mcp" in sys.argv:
        from .integration import mcp_binary
        exe = mcp_binary()
        os.execv(str(exe), [str(exe)])  # replaces this process: no Python left running
    elif "--speak" in sys.argv:
        from .config import load_keys, load_settings
        from . import say
        load_keys()
        text = " ".join(sys.argv[sys.argv.index("--speak") + 1:])
        say.speak(text, load_settings().get("speaking_voice"))
        say.wait()
        return
    elif "--cli" in sys.argv:
        sys.argv.remove("--cli")
        from .__main__ import main as run
    else:
        from .menubar import main as run
    run()


if __name__ == "__main__":
    main()
