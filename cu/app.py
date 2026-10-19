"""Entry point for the packaged app: menu bar by default, `--mcp` for Antigravity/Claude, `--cli` for the CLI."""

import sys


def main():
    if "--mcp" in sys.argv:
        from .mcp_server import main as run
    elif "--cli" in sys.argv:
        sys.argv.remove("--cli")
        from .__main__ import main as run
    else:
        from .menubar import main as run
    run()


if __name__ == "__main__":
    main()
