import io
import json
import tempfile
import unittest
from pathlib import Path

from tests.test_rides import MONDAY, ride
from zwanki.mcp import serve
from zwanki.rides import Recorder, RideStore


def talk(db_path, *messages):
    """Run the MCP server over in-memory pipes; returns its replies by id."""
    stdin = io.StringIO("".join(json.dumps(m) + "\n" for m in messages))
    stdout = io.StringIO()
    serve(stdin, stdout, db_path)
    return {r["id"]: r for r in map(json.loads, stdout.getvalue().splitlines())}


def call(n, name, **arguments):
    return {"jsonrpc": "2.0", "id": n, "method": "tools/call", "params": {"name": name, "arguments": arguments}}


def tool_result(reply):
    return json.loads(reply["result"]["content"][0]["text"])


class McpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "rides.db"

    def tearDown(self):
        self.tmp.cleanup()

    def record_a_ride(self):
        store = RideStore(self.db)
        ride(Recorder(store), MONDAY, 900, power=210, heart_rate=145)
        store.close()

    def test_handshake_and_tools(self):
        replies = talk(self.db,
                       {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test"}}},
                       {"jsonrpc": "2.0", "method": "notifications/initialized"},
                       {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                       {"jsonrpc": "2.0", "id": 3, "method": "nope"})
        self.assertEqual(sorted(replies), [1, 2, 3])
        self.assertEqual(replies[1]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(replies[1]["result"]["serverInfo"]["name"], "zwanki")
        self.assertEqual([t["name"] for t in replies[2]["result"]["tools"]], ["weekly_summary", "list_rides", "get_ride"])
        self.assertEqual(replies[3]["error"]["code"], -32601)

    def test_unknown_protocol_version_gets_ours(self):
        reply = talk(self.db, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}})
        self.assertEqual(reply[1]["result"]["protocolVersion"], "2025-06-18")

    def test_no_rides_yet(self):
        reply = talk(self.db, call(1, "weekly_summary"))[1]
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("No rides recorded yet", reply["result"]["content"][0]["text"])

    def test_tools_on_a_recorded_ride(self):
        self.record_a_ride()
        replies = talk(self.db,
                       call(1, "weekly_summary", weeks=1, week_of="2026-10-14"),
                       call(2, "list_rides", since="2026-10-12", until="2026-10-12"),
                       call(3, "get_ride", ride_id=1, block_seconds=300),
                       call(4, "get_ride", ride_id=99),
                       call(5, "list_rides", since="last tuesday"))
        week = tool_result(replies[1])["weeks"][0]
        self.assertEqual((week["rides"], week["moving_min"], week["avg_power_w"]), (1, 15.0, 210))
        [listed] = tool_result(replies[2])["rides"]
        self.assertEqual((listed["id"], listed["avg_heart_rate_bpm"]), (1, 145))
        detail = tool_result(replies[3])
        self.assertEqual(len(detail["per_300_seconds"]), 3)
        self.assertEqual(detail["trainer_settings"], [{"at_min": 0.0, "setting": "resistance 20%"}])
        self.assertTrue(replies[4]["result"]["isError"])
        self.assertTrue(replies[5]["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
