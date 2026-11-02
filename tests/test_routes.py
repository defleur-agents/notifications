"""Routes: where each source's notifications go, ONE place each: chats, badge, daily (the Morning Report) or off.
Stored on the box so every device agrees. Daily is for Notifications and System notices only, and something that
needs the owner is never held for the morning."""
import json, os, tempfile, time, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOW = 1_800_000_000
MSGS = [
    {"id": "u1", "time": NOW - 3600, "title": "1 app updated", "message": "Updated Tinfoil (0.3.8).", "priority": 3},
    {"id": "u2", "time": NOW - 3500, "title": "App updates need a look", "message": "Could not update Money.", "priority": 3},
    {"id": "c1", "time": NOW - 3400, "title": "Anything", "message": "Money can read SimpleFIN.", "priority": 2,
     "tags": ["link", "wizard-system", "connections"]},
    {"id": "n1", "time": NOW - 3300, "title": "Backup verified", "message": "ok", "priority": 3, "tags": ["cron"]},
    {"id": "old", "time": NOW - 30 * 3600, "title": "2 apps updated", "message": "old", "priority": 3},
]


def load(state):
    os.environ["INBOX_STATE"] = state
    os.environ.pop("INBOX_ROUTES", None)
    src = (ROOT / "mcp/front.py").read_text() + "\n" + (ROOT / "mcp/tools.py").read_text() + "\n" + (ROOT / "mcp/minimal_assets.py").read_text()
    ns = {"__name__": "notif_test"}
    exec(compile(src, "front+tools", "exec"), ns)
    ns["_messages"] = lambda since="all": [dict(m, event="message") for m in MSGS]
    return ns


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ns = load(str(Path(self.tmp.name) / "inbox.json"))
        self.route = self.ns["inbox_route"]

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, method, sub, body=None, query=""):
        raw = json.dumps(body).encode() if body is not None else b""
        code, _, out = self.route(method, "/notifications/" + sub, query, {"content-type": "application/json"}, raw)
        return code, json.loads(out)

    def test_defaults_and_nothing_saved(self):
        code, d = self.call("GET", "api/routes")
        self.assertEqual(code, 200)
        self.assertEqual(d["routes"], {})
        self.assertFalse(d["saved"])
        self.assertEqual(d["defaults"]["notifications"], "chats")
        self.assertEqual(d["defaults"]["system:app-updates"], "daily")

    def test_save_is_one_place_per_source_and_survives_a_reload(self):
        code, d = self.call("POST", "api/routes", {"routes": {"messenger": "badge", "system:connections": "off"}})
        self.assertEqual(code, 200)
        code, d = self.call("POST", "api/routes", {"routes": {"messenger": "chats"}})
        self.assertEqual(d["routes"], {"messenger": "chats", "system:connections": "off"})
        again = load(self.ns["STATE_FILE"])["inbox_routes"]()
        self.assertEqual(again["routes"], {"messenger": "chats", "system:connections": "off"})
        self.assertTrue(again["saved"])

    def test_refused(self):
        for bad in ({"messenger": "daily"}, {"messenger": "mute"}, {"system:nope": "off"}, {"Bad Id": "off"}, {}):
            code, d = self.call("POST", "api/routes", {"routes": bad})
            self.assertEqual(code, 400, bad)
        code, _ = self.call("POST", "api/routes", {"routes": {"notifications": "daily"}})
        self.assertEqual(code, 200, "Notifications' own alerts can be Daily")

    def test_daily_rolls_up_last_day_and_never_a_problem(self):
        real = self.ns["time"].time
        self.ns["time"].time = lambda: NOW
        try:
            code, d = self.call("GET", "api/daily")
        finally:
            self.ns["time"].time = real
        self.assertEqual(code, 200)
        got = {g["source"]: [i["id"] for i in g["items"]] for g in d["groups"]}
        self.assertEqual(got, {"system:app-updates": ["u1"], "system:connections": ["c1"]},
                         "the failed update and the cron result are not rolled up; the 30-hour-old one is out")
        self.assertEqual([g["name"] for g in d["groups"]], ["App updates", "App connections"])

    def test_off_is_not_daily(self):
        self.call("POST", "api/routes", {"routes": {"system:app-updates": "off"}})
        real = self.ns["time"].time
        self.ns["time"].time = lambda: NOW
        try:
            _, d = self.call("GET", "api/daily")
        finally:
            self.ns["time"].time = real
        self.assertEqual([g["source"] for g in d["groups"]], ["system:connections"])


if __name__ == "__main__":
    unittest.main()
