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


def newest_first(msgs):
    """What the real _messages() hands the inbox: newest first (mcp/tools.py sorts by time, descending, stably)."""
    return [dict(m, event="message") for m in sorted(msgs, key=lambda m: -m["time"])]


def load(state):
    os.environ["INBOX_STATE"] = state
    os.environ.pop("INBOX_ROUTES", None)
    src = (ROOT / "mcp/front.py").read_text() + "\n" + (ROOT / "mcp/tools.py").read_text() + "\n" + (ROOT / "mcp/minimal_assets.py").read_text()
    ns = {"__name__": "notif_test"}
    exec(compile(src, "front+tools", "exec"), ns)
    ns["_messages"] = lambda since="all": newest_first(MSGS)
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
        self.assertEqual(d["names"]["system:app-updates"], "App updates", "the Morning Report matches its lines by name")

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
        code, _ = self.call("POST", "api/routes", {"routes": {"system:app-updates": "daily"}})
        self.assertEqual(code, 200, "System notices can be Daily")

    def test_agent_notes_only_wait_in_chats(self):
        """The agent's own alerts (about no app) can't go to Badge, Daily or Off (Miguel, 5 Nov 2026); System keeps its choices."""
        for to in ("badge", "daily", "off"):
            code, d = self.call("POST", "api/routes", {"routes": {"notifications": to}})
            self.assertEqual(code, 400, to)
            self.assertIn("always wait in Chats", d.get("error", ""))
        code, d = self.call("POST", "api/routes", {"routes": {"notifications": "off", "messenger": "badge"}})
        self.assertEqual(code, 400, "a mixed change is refused whole")
        self.assertEqual(self.call("GET", "api/routes")[1]["routes"], {}, "nothing written")
        code, d = self.call("POST", "api/routes", {"routes": {"notifications": "chats", "system:app-updates": "off"}})
        self.assertEqual(code, 200)
        self.assertEqual(d["routes"], {"notifications": "chats", "system:app-updates": "off"})
        # an old routes.json with Off for them reads as Chats; the rest stays as saved
        Path(self.ns["ROUTES_FILE"]).write_text(json.dumps({"routes": {"notifications": "off", "messenger": "badge"}, "saved": True}))
        got = self.ns["inbox_routes"]()
        self.assertEqual(got["routes"], {"notifications": "chats", "messenger": "badge"})
        self.assertFalse(self.ns["_route_ok"]("notifications", "badge"))
        self.assertTrue(self.ns["_route_ok"]("messenger", "off"))

    def test_agent_notes_never_roll_into_the_morning_report(self):
        Path(self.ns["ROUTES_FILE"]).write_text(json.dumps({"routes": {"notifications": "daily"}, "saved": True}))
        real = self.ns["time"].time
        self.ns["time"].time = lambda: NOW
        try:
            _, d = self.call("GET", "api/daily")
        finally:
            self.ns["time"].time = real
        self.assertNotIn("notifications", [g["source"] for g in d["groups"]])

    def test_report_sections_are_daily_or_off(self):
        """Morning Report sections (report:<id>) default to the report; Off drops them; Chats/Badge are refused for now."""
        code, d = self.call("GET", "api/routes")
        self.assertEqual(d["report"], {"prefix": "report:", "default": "daily", "allowed": ["daily", "off"]})
        code, d = self.call("POST", "api/routes", {"routes": {"report:wizard-health": "off", "report:wizard-calendar.today": "daily"}})
        self.assertEqual(code, 200)
        self.assertEqual(d["routes"], {"report:wizard-health": "off", "report:wizard-calendar.today": "daily"})
        for bad in ({"report:wizard-health": "chats"}, {"report:wizard-health": "badge"}, {"report:Bad": "off"},
                    {"report:": "off"}, {"report:a.b.c": "off"}, {"report:x/y": "off"}):
            code, _ = self.call("POST", "api/routes", {"routes": bad})
            self.assertEqual(code, 400, bad)
        again = load(self.ns["STATE_FILE"])["inbox_routes"]()
        self.assertEqual(again["routes"]["report:wizard-health"], "off")
        # a hand-edited routes.json with a Chats report section is cleaned on read
        Path(self.ns["ROUTES_FILE"]).write_text(json.dumps({"routes": {"report:wizard-health": "chats", "messenger": "badge"}, "saved": True}))
        self.assertEqual(self.ns["inbox_routes"]()["routes"], {"messenger": "badge"})

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
        # Groups follow the inbox: the source with the newest item first (connections c1 is newer than updates u1).
        self.assertEqual([g["name"] for g in d["groups"]], ["App connections", "App updates"])

    def daily_groups(self, feed):
        """Daily groups when the feed is handed over in exactly this order (not pre-sorted)."""
        self.ns["_messages"] = lambda since="all": [dict(m, event="message") for m in feed]
        real = self.ns["time"].time
        self.ns["time"].time = lambda: NOW
        try:
            code, d = self.call("GET", "api/daily")
        finally:
            self.ns["time"].time = real
        self.assertEqual(code, 200)
        return [g["name"] for g in d["groups"]]

    def test_daily_group_order_is_newest_first_whatever_order_the_feed_arrives_in(self):
        import itertools
        for feed in itertools.permutations(MSGS):
            self.assertEqual(self.daily_groups(feed), ["App connections", "App updates"], [m["id"] for m in feed])

    def test_daily_group_order_ties_keep_the_feed_order(self):
        tied = [dict(m, time=NOW - 3000) for m in MSGS if m["id"] in ("u1", "c1")]
        self.assertEqual(self.daily_groups(tied), ["App updates", "App connections"])
        self.assertEqual(self.daily_groups(tied[::-1]), ["App connections", "App updates"])
        self.assertEqual(self.daily_groups(tied), self.daily_groups(tied), "same feed, same order")

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
