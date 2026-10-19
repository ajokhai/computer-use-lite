"""Hooking the app into the rest of the Mac: open at login, and Antigravity/Claude MCP configs."""

import json
import plistlib
import subprocess
import sys
from pathlib import Path

LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / "com.computeruse.lite.plist"
_SUPPORT = Path.home() / "Library" / "Application Support"

# name -> [(config file, top-level key)]. VS Code uses "servers" (+ "type": "stdio"); the rest "mcpServers".
CLIENTS = {
    "Antigravity": [(Path.home() / ".gemini" / "antigravity" / "mcp_config.json", "mcpServers"),
                    (Path.home() / ".gemini" / "config" / "mcp_config.json", "mcpServers")],
    "VS Code": [(_SUPPORT / "Code" / "User" / "mcp.json", "servers"),
                (_SUPPORT / "Code - Insiders" / "User" / "mcp.json", "servers")],
    "Cursor": [(Path.home() / ".cursor" / "mcp.json", "mcpServers")],
    "Windsurf": [(Path.home() / ".codeium" / "windsurf" / "mcp_config.json", "mcpServers")],
    "Claude": [(_SUPPORT / "Claude" / "claude_desktop_config.json", "mcpServers"),  # desktop app
               (Path.home() / ".claude.json", "mcpServers")],  # Claude Code, user scope
}
SERVER_NAME = "computer-use-lite"
PROJECT = Path(__file__).resolve().parent.parent


def app_bundle() -> Path | None:
    """The .app this is running from, when packaged."""
    if getattr(sys, "frozen", False):
        for parent in Path(sys.executable).resolve().parents:
            if parent.suffix == ".app":
                return parent
    return None


def mcp_command() -> dict:
    bundle = app_bundle()
    if bundle:
        return {"command": str(bundle / "Contents" / "MacOS" / bundle.stem), "args": ["--mcp"]}
    uv = Path.home() / ".local" / "bin" / "uv"
    return {"command": str(uv), "args": ["run", "--directory", str(PROJECT), "python", "-m", "cu.mcp_server"]}


# --- open at login ---------------------------------------------------------

def login_enabled() -> bool:
    return LAUNCH_AGENT.exists()


def set_login(enabled: bool):
    bundle = app_bundle()
    if enabled and bundle:
        LAUNCH_AGENT.parent.mkdir(parents=True, exist_ok=True)
        with LAUNCH_AGENT.open("wb") as f:
            plistlib.dump({"Label": "com.computeruse.lite", "RunAtLoad": True,
                           "ProgramArguments": ["/usr/bin/open", "-a", str(bundle)]}, f)
    elif not enabled:
        LAUNCH_AGENT.unlink(missing_ok=True)


# --- MCP clients (Antigravity, Claude) ---------------------------------------

def _entry(key: str) -> dict:
    return {"type": "stdio", **mcp_command()} if key == "servers" else mcp_command()


def _targets(client: str):
    """Config files that belong to an installed copy of the client."""
    for path, key in CLIENTS[client]:
        # ~/.claude.json only exists once Claude Code has run; never create it ourselves.
        if path.parent.exists() and (path.exists() or path.name != ".claude.json"):
            yield path, key


def installed(client: str) -> bool:
    return any(True for _ in _targets(client))


def connected(client: str) -> bool:
    for path, key in _targets(client):
        try:
            if json.loads(path.read_text()).get(key, {}).get(SERVER_NAME) == _entry(key):
                return True
        except (OSError, ValueError):
            pass
    return False


def connect(client: str, enabled: bool = True):
    """Add (or remove) this app's MCP server in the client's configs, keeping a one-time backup."""
    for path, key in _targets(client):
        _write(path, key, enabled)


def _write(path: Path, key: str, enabled: bool):
    try:
        data = json.loads(path.read_text())
    except OSError:
        data = {}
    except ValueError:
        return  # never clobber a file we can't parse (VS Code allows comments in mcp.json)
    backup = path.with_name(path.name + ".bak-cu")
    if path.exists() and not backup.exists():
        backup.write_text(path.read_text())
    servers = data.setdefault(key, {})
    if enabled:
        servers[SERVER_NAME] = _entry(key)
    else:
        servers.pop(SERVER_NAME, None)
    tmp = path.with_name(path.name + ".tmp-cu")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)  # atomic, so a running app never reads a half-written file


def refresh_connections():
    """Repoint existing entries at this copy of the app (e.g. after moving from the dev folder to the .app).
    Only an installed copy does this, so running from the DMG or a build folder never steals the entries."""
    bundle = app_bundle()
    if bundle is None or bundle.parent not in (Path("/Applications"), Path.home() / "Applications"):
        return
    for client in CLIENTS:
        for path, key in _targets(client):
            try:
                current = json.loads(path.read_text()).get(key, {}).get(SERVER_NAME)
            except (OSError, ValueError):
                continue
            if current is not None and current != _entry(key):
                _write(path, key, True)


def open_privacy_settings(pane: str):
    """pane: 'Accessibility' or 'ScreenCapture'."""
    subprocess.run(["open", f"x-apple.systempreferences:com.apple.preference.security?Privacy_{pane}"])
