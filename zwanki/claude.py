"""Connecting Claude to Zwanki's rides: the MCP command for this install, and adding it to Claude Desktop."""

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

SERVER_NAME = "zwanki"


def mcp_command():
    """(command, args) that start Zwanki's MCP server from wherever this copy is installed."""
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        if sys.platform == "win32":  # the main exe has no console, so stdin/stdout go to a twin that does
            exe = exe.with_name("zwanki-mcp.exe")
        return str(exe), ["--mcp"]
    return sys.executable, [str(Path(__file__).resolve().parent.parent / "main.py"), "--mcp"]


def claude_desktop_config_path():
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "Claude" / "claude_desktop_config.json"
    return None  # no official Claude Desktop for Linux


def claude_code_command():
    command, args = mcp_command()
    join = subprocess.list2cmdline if sys.platform == "win32" else shlex.join
    return join(["claude", "mcp", "add", SERVER_NAME, "--scope", "user", "--", command, *args])


def status():
    command, args = mcp_command()
    path = claude_desktop_config_path()
    entry = None
    if path and path.exists():
        try:
            entry = json.loads(path.read_text(encoding="utf-8")).get("mcpServers", {}).get(SERVER_NAME)
        except (OSError, ValueError, AttributeError):
            pass
    return {
        "command": command,
        "args": args,
        "claude_code": claude_code_command(),
        "desktop_config": str(path) if path else None,
        "desktop_available": bool(path and path.parent.exists()),
        "desktop_added": entry == {"command": command, "args": args},
        "desktop_outdated": entry is not None and entry != {"command": command, "args": args},
    }


def add_to_claude_desktop():
    """Add (or update) Zwanki in Claude Desktop's MCP servers; everything else in the file is kept,
    and the previous version is saved next to it as .bak. Claude Desktop must be restarted afterwards."""
    path = claude_desktop_config_path()
    if not path or not path.parent.exists():
        raise RuntimeError("Claude Desktop doesn't seem to be installed")
    config = {}
    if path.exists():
        text = path.read_text(encoding="utf-8")
        config = json.loads(text) if text.strip() else {}
        if not isinstance(config, dict):
            raise RuntimeError(f"{path.name} isn't a JSON object; not touching it")
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    command, args = mcp_command()
    config.setdefault("mcpServers", {})[SERVER_NAME] = {"command": command, "args": args}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return str(path)
