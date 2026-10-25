"""Connecting AI apps: an installed copy connects every installed client on the first run, renames cu-mcp
entries added by hand under another name (Claude Code skips the reserved "computer-use"), and leaves other
servers and later choices alone."""

import json
from pathlib import Path

import pytest

from cu import integration

APP = Path("/Applications/Computer Use.app")
CU_MCP = str(APP / "Contents" / "Frameworks" / "cu-mcp")


@pytest.fixture
def clients(tmp_path, monkeypatch):
    claude_code = tmp_path / ".claude.json"
    cursor = tmp_path / ".cursor" / "mcp.json"
    cursor.parent.mkdir()
    claude_code.write_text(json.dumps({"numStartups": 3, "mcpServers": {
        "computer-use": {"type": "stdio", "command": CU_MCP, "args": [], "env": {}},
        "other": {"command": "/usr/local/bin/other"}}}))
    monkeypatch.setattr(integration, "CLIENTS", {"Claude": [(claude_code, "mcpServers")],
                                                 "Cursor": [(cursor, "mcpServers")],
                                                 "Windsurf": [(tmp_path / "absent" / "mcp.json", "mcpServers")]})
    monkeypatch.setattr(integration, "app_bundle", lambda: APP)
    monkeypatch.setattr(integration, "mcp_binary", lambda: Path(CU_MCP))
    return claude_code, cursor


def servers(path):
    return json.loads(path.read_text())["mcpServers"]


def test_reserved_name_is_renamed_and_others_kept(clients):
    claude_code, _ = clients
    assert integration.refresh_connections()
    assert servers(claude_code) == {"other": {"command": "/usr/local/bin/other"},
                                    "computer-use-lite": {"command": CU_MCP, "args": []}}
    assert json.loads(claude_code.read_text())["numStartups"] == 3
    assert integration.connected("Claude")


def test_first_run_connects_installed_clients_only(clients, tmp_path):
    _, cursor = clients
    assert not cursor.exists()
    integration.refresh_connections()
    assert not cursor.exists()  # not the first run: an unticked client stays unticked
    integration.refresh_connections(first_run=True)
    assert servers(cursor) == {"computer-use-lite": {"command": CU_MCP, "args": []}}
    assert not (tmp_path / "absent").exists()


def test_not_installed_copy_touches_nothing(clients, monkeypatch):
    claude_code, cursor = clients
    before = claude_code.read_text()
    monkeypatch.setattr(integration, "app_bundle", lambda: Path("/Volumes/Computer Use/Computer Use.app"))
    assert not integration.refresh_connections(first_run=True)
    assert claude_code.read_text() == before and not cursor.exists()


def test_disconnect_removes_misnamed_entry_too(clients):
    claude_code, _ = clients
    integration.connect("Claude", False)
    assert servers(claude_code) == {"other": {"command": "/usr/local/bin/other"}}
