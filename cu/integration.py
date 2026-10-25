"""Hooking the app into the rest of the Mac: open at login, and the MCP configs of AI apps
(Antigravity, VS Code, Cursor, Windsurf, Claude) so any of them can drive the Mac with cu-mcp."""

import json
import plistlib
import subprocess
import sys
from pathlib import Path

LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / "com.computeruse.lite.plist"
_SUPPORT = Path.home() / "Library" / "Application Support"

# name -> [(config file, top-level key)]. VS Code uses "servers" (+ "type": "stdio"); the rest "mcpServers".
CLIENTS = {
    "Antigravity": [(Path.home() / ".gemini" / "antigravity-ide" / "mcp_config.json", "mcpServers"),  # Antigravity IDE
                    (Path.home() / ".gemini" / "antigravity" / "mcp_config.json", "mcpServers"),
                    (Path.home() / ".gemini" / "config" / "mcp_config.json", "mcpServers")],
    "VS Code": [(_SUPPORT / "Code" / "User" / "mcp.json", "servers"),
                (_SUPPORT / "Code - Insiders" / "User" / "mcp.json", "servers")],
    "Cursor": [(Path.home() / ".cursor" / "mcp.json", "mcpServers")],
    "Windsurf": [(Path.home() / ".codeium" / "windsurf" / "mcp_config.json", "mcpServers")],
    "Claude": [(_SUPPORT / "Claude" / "claude_desktop_config.json", "mcpServers"),  # desktop app
               (Path.home() / ".claude.json", "mcpServers")],  # Claude Code, user scope
}
SERVER_NAME = "computer-use-lite"  # never "computer-use": Claude Code reserves that name and skips the server
PROJECT = Path(__file__).resolve().parent.parent


def app_bundle() -> Path | None:
    """The .app this is running from, when packaged."""
    if getattr(sys, "frozen", False):
        for parent in Path(sys.executable).resolve().parents:
            if parent.suffix == ".app":
                return parent
    return None


def mcp_binary() -> Path:
    """The native MCP server: bundled in the app, or built in the development checkout."""
    bundle = app_bundle()
    if bundle:
        for p in (bundle / "Contents" / "Frameworks" / "cu-mcp", bundle / "Contents" / "MacOS" / "cu-mcp"):
            if p.is_file():
                return p
    return PROJECT / "crates" / "cu-native" / "target" / "release" / "cu-mcp"


def mcp_command() -> dict:
    return {"command": str(mcp_binary()), "args": []}


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
    for name in [n for n, v in servers.items() if n != SERVER_NAME and _is_ours(v)]:
        del servers[name]  # cu-mcp added by hand under another name, e.g. the reserved "computer-use"
    if enabled:
        servers[SERVER_NAME] = _entry(key)
    else:
        servers.pop(SERVER_NAME, None)
    tmp = path.with_name(path.name + ".tmp-cu")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)  # atomic, so a running app never reads a half-written file


def _is_ours(server) -> bool:
    return isinstance(server, dict) and Path(str(server.get("command", ""))).name == "cu-mcp"


def refresh_connections(first_run: bool = False) -> bool:
    """Repoint existing entries at this copy of the app (e.g. after moving from the dev folder to the .app),
    and rename ones added by hand under another name. On the first run, connect every installed AI app, so
    it works without a trip to Connect AI Apps. Only an installed copy does this, so running from the DMG
    or a build folder never steals the entries. Returns whether it ran."""
    bundle = app_bundle()
    if bundle is None or bundle.parent not in (Path("/Applications"), Path.home() / "Applications"):
        return False
    for client in CLIENTS:
        for path, key in _targets(client):
            try:
                servers = json.loads(path.read_text()).get(key, {})
            except OSError:
                servers = {}
            except ValueError:
                continue
            ours = {n: v for n, v in servers.items() if _is_ours(v)}
            if (first_run and not ours) or (ours and ours != {SERVER_NAME: _entry(key)}):
                _write(path, key, True)
    return True


def open_privacy_settings(pane: str):
    """pane: 'Accessibility' or 'ScreenCapture'."""
    subprocess.run(["open", f"x-apple.systempreferences:com.apple.preference.security?Privacy_{pane}"])
