"""Zwanki's rides for Claude: a small, read-only MCP server speaking JSON-RPC on stdin/stdout.

Claude Desktop or Claude Code starts it as `zwanki --mcp` (the settings page shows the exact
command and can add it to Claude Desktop). It reads the ride database directly, so the Zwanki
app doesn't need to be running.
"""

import json
import sys
from datetime import date, datetime, timedelta

from . import __version__
from .config import RIDES_PATH
from .rides import RideStore, downsample, summarize, weekly_summary

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")  # newest first

INSTRUCTIONS = """Zwanki records indoor rides on a smart trainer (Wahoo KICKR) with heart rate from a chest strap.
A ride starts when the rider pedals and ends after 5 minutes without pedaling. Times are local.
For a weekly review, start with weekly_summary, then look at individual rides with get_ride."""

TOOLS = [
    {
        "name": "weekly_summary",
        "description": "Training totals per week (Monday to Sunday), newest first: number of rides, moving time, "
                       "distance, energy, time-weighted average power and heart rate, and the ride ids for drilling down.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "weeks": {"type": "integer", "minimum": 1, "maximum": 52, "default": 4,
                          "description": "How many weeks to include."},
                "week_of": {"type": "string", "format": "date",
                            "description": "Any date (YYYY-MM-DD) in the newest week to include. Default: today."},
            },
        },
    },
    {
        "name": "list_rides",
        "description": "Rides with their key numbers (duration, distance, energy, average/max/normalized power, "
                       "cadence, speed, heart rate), newest first.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "since": {"type": "string", "format": "date", "description": "First day (YYYY-MM-DD). Default: 28 days ago."},
                "until": {"type": "string", "format": "date", "description": "Last day (YYYY-MM-DD), inclusive. Default: today."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
            },
        },
    },
    {
        "name": "get_ride",
        "description": "One ride in detail: its key numbers, when the trainer settings (resistance %, hill grade %, "
                       "ERG watts) changed and for how long each was ridden, and the ride averaged over time blocks.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ride_id": {"type": "integer"},
                "block_seconds": {"type": "integer", "minimum": 5, "maximum": 3600, "default": 60,
                                  "description": "Length of the time blocks the ride is averaged over."},
            },
            "required": ["ride_id"],
        },
    },
]


class ToolError(Exception):
    pass


def parse_day(value, name):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ToolError(f"{name} must be a date like 2026-10-19")


def day_start(day):
    return datetime.combine(day, datetime.min.time()).timestamp()


def call_tool(store, name, args):
    if name == "weekly_summary":
        weeks = int(args.get("weeks", 4))
        week_of = parse_day(args["week_of"], "week_of") if "week_of" in args else None
        return {"weeks": weekly_summary(store, max(1, min(weeks, 52)), week_of)}
    if name == "list_rides":
        until = parse_day(args["until"], "until") if "until" in args else date.today()
        since = parse_day(args["since"], "since") if "since" in args else until - timedelta(days=27)
        rides = store.rides(day_start(since), day_start(until + timedelta(days=1)), int(args.get("limit", 50)))
        return {"from": since.isoformat(), "to": until.isoformat(),
                "rides": [summarize(r, store.samples(r["id"])) for r in rides]}
    if name == "get_ride":
        ride = store.ride(args.get("ride_id"))
        if ride is None:
            raise ToolError(f"there is no ride {args.get('ride_id')}")
        samples = store.samples(ride["id"])
        block = max(5, min(int(args.get("block_seconds", 60)), 3600))
        return {**summarize(ride, samples, store.settings(ride["id"])),
                f"per_{block}_seconds": downsample(ride, samples, block)}
    raise ToolError(f"unknown tool {name}")


class Server:
    def __init__(self, db_path=RIDES_PATH):
        self.db_path = db_path
        self._store = None

    def store(self):
        if self._store is None:
            if not self.db_path.exists():
                raise ToolError("No rides recorded yet. Zwanki records a ride when its trainer is connected and you pedal.")
            self._store = RideStore(self.db_path, readonly=True)
        return self._store

    def close(self):
        if self._store is not None:
            self._store.close()
            self._store = None

    def handle(self, message):
        """Answer one JSON-RPC message; returns None for notifications."""
        if not isinstance(message, dict):
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
        method, msg_id, params = message.get("method"), message.get("id"), message.get("params") or {}
        if msg_id is None:
            return None  # notifications (initialized, cancelled) need no answer
        try:
            result = self.dispatch(method, params)
        except LookupError:
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"unknown method {method}"}}
        except Exception as e:  # never let one bad request take the server down
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32603, "message": f"{type(e).__name__}: {e}"}}
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def dispatch(self, method, params):
        if method == "initialize":
            asked = params.get("protocolVersion")
            return {
                "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "zwanki", "version": __version__},
                "instructions": INSTRUCTIONS,
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": TOOLS}
        if method == "tools/call":
            try:
                data = call_tool(self.store(), params.get("name"), params.get("arguments") or {})
                return {"content": [{"type": "text", "text": json.dumps(data, indent=1)}]}
            except (ToolError, ValueError, TypeError) as e:
                return {"content": [{"type": "text", "text": str(e)}], "isError": True}
        raise LookupError(method)


def serve(stdin=sys.stdin, stdout=sys.stdout, db_path=RIDES_PATH):
    """Read newline-delimited JSON-RPC from stdin until it closes. Nothing else may go to stdout."""
    server = Server(db_path)
    try:
        _serve(server, stdin, stdout)
    finally:
        server.close()  # Windows won't let the app touch the database while it's open


def _serve(server, stdin, stdout):
    for line in stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            if isinstance(message, list):  # batches, from the 2025-03-26 protocol
                reply = [r for r in map(server.handle, message) if r] or None
            else:
                reply = server.handle(message)
        if reply:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()
