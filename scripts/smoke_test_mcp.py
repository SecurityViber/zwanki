"""Check that a built Zwanki answers as an MCP server: python scripts/smoke_test_mcp.py <command> [args...]"""

import json
import subprocess
import sys

requests = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
     "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "smoke-test", "version": "0"}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "list_rides", "arguments": {}}},
]
stdin = "".join(json.dumps(r) + "\n" for r in requests)
out = subprocess.run(sys.argv[1:], input=stdin, capture_output=True, text=True, timeout=60)
replies = {r["id"]: r for r in map(json.loads, out.stdout.splitlines())}
assert replies[1]["result"]["serverInfo"]["name"] == "zwanki", replies
assert [t["name"] for t in replies[2]["result"]["tools"]] == ["weekly_summary", "list_rides", "get_ride"], replies
assert "content" in replies[3]["result"], replies  # on a fresh machine: "no rides recorded yet"
print("MCP smoke test ok:", replies[3]["result"]["content"][0]["text"][:80])
