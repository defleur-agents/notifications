"""Phone router: apps' inbox feeds -> one event stream for the Wizard OS Android app (no Google push).

Covers the diff rules (notify / quiet update / clear / count), the owner's routes (only Chats reaches the phone, Badge
is a count, Off nothing, moving an app out of Chats takes its notifications away), catch-up (since-cursor, reset on
a new store or a too-old cursor), who may send a feed (the app's own link network only) and who may read (Tailscale
user identity, never another app's container), and the real streaming endpoint over a socket: a feed posted while
the phone listens arrives as an SSE event at once, and a reconnect with the last id replays exactly what was missed.
"""
import http.client
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = ("front.py", "tools.py", "minimal_assets.py", "relay.py", "phone.py")


def load(tmp, env=None):
    os.environ.update({"INBOX_STATE": str(Path(tmp) / "inbox.json"), "PHONE_PING_S": "1", **(env or {})})
    os.environ.pop("INBOX_ROUTES", None)
    src = "\n".join((ROOT / "mcp" / n).read_text() for n in FILES)
    ns = {"__name__": "phone_test"}
    exec(compile(src, "front+tools+relay+phone", "exec"), ns)
    for k in ("PHONE_PING_S", *(env or {})):
        os.environ.pop(k, None)
    return ns


def feed(*items):
    return {"inbox": 1, "items": [dict({"priority": 3, "actions": [
        {"id": "reply", "label": "Reply", "input": True},
        {"id": "draft", "label": "Draft me a reply", "ask": "Draft a reply"},
        {"id": "read", "label": "Mark as read"}]}, **i) for i in items]}


CARLOS = {"key": "chat:carlos", "title": "Carlos", "text": "See you at 5", "time": 1000}


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ns = load(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def route(self, app, to):
        self.ns["set_inbox_routes"]({app: to})

    def events(self, since=0):
        out = self.ns["phone_since"](since, self.sid())
        self.assertFalse(out.get("reset"), out)
        return [e for _, e in out["events"]]

    def sid(self):
        return self.ns["phone_since"](0, "")["sid"]

    def test_agent_notes_route_as_chats(self):
        Path(self.ns["ROUTES_FILE"]).write_text(json.dumps({"routes": {"notifications": "off"}, "saved": True}))
        self.assertEqual(self.ns["phone_route"]("notifications"), "chats")

    def test_apps_are_badges_until_routed_to_chats(self):
        self.assertEqual(self.ns["phone_route"]("messenger"), "badge")
        self.assertEqual(self.ns["ingest_feed"]("messenger", feed(CARLOS)), 1)
        self.assertEqual(self.events(), [{"type": "count", "app": "messenger", "count": 1}], "a badge carries no text")
        db = self.ns["_db"]()
        self.assertIsNone(db.execute("SELECT data FROM items").fetchone()[0], "a Badge app's words are never stored")
        db.close()

    def test_first_feed_is_a_quiet_backlog(self):
        self.route("messenger", "chats")
        self.ns["ingest_feed"]("messenger", feed(CARLOS, {"key": "chat:ana", "title": "Ana", "text": "old", "time": 900}))
        ev = self.events()
        self.assertEqual([(e["key"], e["alert"]) for e in ev], [("chat:carlos", False), ("chat:ana", False)],
                         "an app's first feed (everything already unread) shows without a buzz per chat")
        self.ns["ingest_feed"]("messenger", feed(CARLOS, {"key": "chat:ana", "title": "Ana", "text": "new", "time": 950}))
        self.assertTrue(self.events()[-1]["alert"], "after that, newer activity alerts")

    def test_chats_notifies_new_then_newer_then_quiet_reword_then_clear(self):
        self.route("messenger", "chats")
        self.ns["ingest_feed"]("messenger", feed())          # Messenger's start-up feed: nothing unread yet
        self.ns["ingest_feed"]("messenger", feed(CARLOS))
        ev = self.events()
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["type"], "notify")
        self.assertEqual(ev[0]["app"], "messenger")
        self.assertTrue(ev[0]["alert"])
        self.assertEqual([a["id"] for a in ev[0]["actions"]], ["reply", "read"], "an agent ask stays off the phone")
        self.assertTrue(ev[0]["actions"][0]["input"])
        # Same feed again: nothing new.
        self.assertEqual(self.ns["ingest_feed"]("messenger", feed(CARLOS)), 0)
        # Newer message in the same chat alerts again; a re-worded preview of the same message is quiet.
        self.ns["ingest_feed"]("messenger", feed(dict(CARLOS, text="2 new · Running late", time=1010)))
        self.ns["ingest_feed"]("messenger", feed(dict(CARLOS, text="2 new · Llego tarde", time=1010)))
        ev = self.events()
        self.assertEqual([(e["type"], e.get("alert")) for e in ev], [("notify", True), ("notify", True), ("notify", False)])
        # Read on the desktop: the chat leaves the feed and the phone notification goes.
        self.ns["ingest_feed"]("messenger", feed())
        self.assertEqual(self.events()[-1], {"type": "clear", "app": "messenger", "key": "chat:carlos"})

    def test_off_and_unrouted_apps_never_reach_the_phone(self):
        self.route("messenger", "off")
        self.assertEqual(self.ns["ingest_feed"]("messenger", feed(CARLOS)), 0)
        self.assertEqual(self.events(), [])
        with self.assertRaises(ValueError):
            self.ns["set_inbox_routes"]({"messenger": "daily"})   # Morning report stays greyed out for apps

    def test_moving_an_app_out_of_chats_takes_its_notifications_away(self):
        self.route("messenger", "chats")
        self.ns["ingest_feed"]("messenger", feed(CARLOS))
        self.route("messenger", "badge")
        self.assertEqual(self.events()[-1], {"type": "clear", "app": "messenger", "key": "chat:carlos"})
        db = self.ns["_db"]()
        self.assertIsNone(db.execute("SELECT data FROM items").fetchone()[0])
        db.close()
        snap = self.ns["phone_since"](0, "x")
        self.assertEqual(snap["items"], [], "a reset no longer shows it")

    def test_catch_up_since_cursor_and_resets(self):
        self.route("messenger", "chats")
        self.ns["ingest_feed"]("messenger", feed(CARLOS))
        sid = self.sid()
        first = self.ns["phone_since"](0, sid)["latest"]
        self.ns["ingest_feed"]("messenger", feed(CARLOS, {"key": "chat:ana", "title": "Ana", "text": "hi", "time": 1100}))
        out = self.ns["phone_since"](first, sid)
        self.assertEqual([e["key"] for _, e in out["events"]], ["chat:ana"], "exactly what was missed")
        # Unknown store (new box, wiped data) or a cursor from the future: a full snapshot instead.
        for since, s in ((first, "other"), (999, sid), ("", sid)):
            snap = self.ns["phone_since"](since, s)
            self.assertTrue(snap["reset"])
            self.assertEqual(sorted(i["key"] for i in snap["items"]), ["chat:ana", "chat:carlos"])
        # A cursor older than what is kept also resets.
        db = self.ns["_db"]()
        db.execute("DELETE FROM events WHERE id <= ?", (first + 0,))
        db.commit(); db.close()
        self.assertFalse(self.ns["phone_since"](first, sid).get("reset"), "the next id is still there")
        self.assertTrue(self.ns["phone_since"](first - 1, sid)["reset"])

    def test_bad_feeds_and_items_are_refused_or_dropped(self):
        with self.assertRaises(ValueError):
            self.ns["ingest_feed"]("messenger", {"items": []})
        with self.assertRaises(ValueError):
            self.ns["ingest_feed"]("Bad App", feed())
        items = self.ns["clean_feed_items"]([{"key": ""}, {"key": "x" * 201}, "nope", {"key": "ok", "time": "x"},
                                             {"key": "fine", "time": 5, "title": "  A\n B ", "priority": 9,
                                              "actions": [{"id": "BAD ID"}, {"id": "go", "label": "x" * 99}]}])
        self.assertEqual(list(items), ["fine"])
        self.assertEqual(items["fine"]["title"], "A B")
        self.assertEqual(items["fine"]["priority"], 5)
        self.assertEqual(items["fine"]["actions"], [{"id": "go", "label": "x" * 40}])


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ns = load(self.tmp.name)
        self.names = {}
        self.ns["peer_name"] = lambda ip: self.names.get(ip, "")

    def tearDown(self):
        self.tmp.cleanup()

    def post(self, headers, body):
        code, _, out = self.ns["phone_feed_route"]("POST", "/notifications/api/app-feed", "",
                                                    dict({"content-type": "application/json"}, **headers), json.dumps(body).encode())
        return code, json.loads(out)

    def test_feed_sender_is_the_app_named_by_its_link_network(self):
        self.names["10.255.0.3"] = "messenger_wizardapps-messenger-1.wizard-link-messenger--notifications"
        code, out = self.post({":peer": "10.255.0.3"}, feed(CARLOS))
        self.assertEqual((code, out["app"]), (200, "messenger"))
        # Same container name on another app's link: that app, never the name it chose for itself.
        self.names["10.255.0.9"] = "messenger_wizardapps-messenger-1.wizard-link-evil--notifications"
        self.assertEqual(self.post({":peer": "10.255.0.9"}, feed(CARLOS))[1]["app"], "evil")
        # The host, the tailnet, a container on any other network, Notifications itself: refused.
        self.names["10.128.0.5"] = "x_wizardapps-x-1.runtipi_tipi_main_network"
        self.names["10.255.0.4"] = "n.wizard-link-notifications--notifications"
        for peer in ("10.128.17.1", "10.128.0.5", "10.255.0.4"):
            self.assertEqual(self.post({":peer": peer}, feed(CARLOS))[0], 403, peer)
        code, _ = self.post({":peer": "10.255.0.3", "tailscale-user-login": "a@b"}, feed(CARLOS))
        self.assertEqual(code, 403, "a request through Tailscale Serve is never an app")

    def test_reader_is_a_tailscale_user_never_a_container(self):
        ok = self.ns["phone_reader_ok"]
        self.assertTrue(ok({":peer": "10.128.17.1", "tailscale-user-login": "Miguel@askmiguel.ai"}))
        self.assertFalse(ok({":peer": "10.128.17.1"}), "a tagged node or plain loopback call has no user")
        self.names["10.255.0.3"] = "messenger_wizardapps-messenger-1.wizard-link-messenger--notifications"
        self.assertFalse(ok({":peer": "10.255.0.3", "tailscale-user-login": "miguel@askmiguel.ai"}),
                         "an app can send any header; its own container address gives it away")
        pinned = load(self.tmp.name, {"PHONE_OWNER_LOGIN": "miguel@askmiguel.ai"})
        pinned["peer_name"] = lambda ip: ""
        self.assertTrue(pinned["phone_reader_ok"]({":peer": "h", "tailscale-user-login": "miguel@askmiguel.ai"}))
        self.assertFalse(pinned["phone_reader_ok"]({":peer": "h", "tailscale-user-login": "guest@else.com"}))
        code, _, _ = self.ns["phone_info_route"]("GET", "/notifications/api/phone/events", "since=0", {":peer": "h"}, b"")
        self.assertEqual(code, 403)
        code, _, out = self.ns["phone_info_route"]("GET", "/notifications/api/phone", "", {}, b"")
        self.assertEqual((code, json.loads(out)["phone"]), (200, 1))

    def test_real_peer_name_reads_docker_dns_and_ignores_plain_addresses(self):
        ns = load(self.tmp.name)
        self.assertEqual(ns["peer_name"]("203.0.113.9"), "", "no PTR: not a container")
        self.assertIsNone(ns["peer_app"]("203.0.113.9"))


class StreamTests(unittest.TestCase):
    """The real front on a socket: SSE through the same handle() that serves the app."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        s = socket.socket(); s.bind(("127.0.0.1", 0)); self.port = s.getsockname()[1]; s.close()
        self.ns = load(self.tmp.name, {"FRONT_PORT": str(self.port)})
        self.ns["peer_name"] = lambda ip: ""
        self.ns["set_inbox_routes"]({"messenger": "chats"})
        self.ns["ingest_feed"]("messenger", feed())
        import asyncio
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=lambda: self.loop.run_until_complete(self.ns["main"]()), daemon=True).start()
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", self.port), 0.2).close(); break
            except OSError:
                time.sleep(0.05)

    def tearDown(self):
        self.tmp.cleanup()

    def open(self, query="", login="miguel@askmiguel.ai"):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", "/notifications/api/phone/stream" + query, headers={"Tailscale-User-Login": login} if login else {})
        return c, c.getresponse()

    def read_event(self, r):
        lines = {}
        while True:
            line = r.fp.readline().decode().rstrip("\n")
            if line == "":
                if "data" in lines:
                    return lines
                continue
            if line.startswith(":"):
                lines.setdefault("pings", 0)
                continue
            k, _, v = line.partition(": ")
            lines[k] = v

    def test_live_event_then_reconnect_replays_what_was_missed(self):
        c, r = self.open()
        self.assertEqual(r.status, 200)
        self.assertTrue(r.getheader("Content-Type").startswith("text/event-stream"))
        hello = self.read_event(r)
        self.assertEqual(hello["event"], "hello")
        sid = json.loads(hello["data"])["sid"]
        self.assertEqual(self.read_event(r)["event"], "reset", "a phone without a cursor starts from a snapshot")
        t0 = time.time()
        threading.Thread(target=lambda: self.ns["ingest_feed"]("messenger", feed(CARLOS))).start()
        ev = self.read_event(r)
        self.assertLess(time.time() - t0, 1.0, "instant, not a poll")
        self.assertEqual(ev["event"], "notify")
        self.assertEqual(json.loads(ev["data"])["title"], "Carlos")
        self.assertTrue(json.loads(ev["data"])["alert"])
        last = int(ev["id"])
        c.close()
        # Offline (Tailscale off): two more things happen.
        self.ns["ingest_feed"]("messenger", feed(dict(CARLOS, time=1001), {"key": "chat:ana", "title": "Ana", "text": "hi", "time": 1100}))
        self.ns["ingest_feed"]("messenger", feed({"key": "chat:ana", "title": "Ana", "text": "hi", "time": 1100}))
        c, r = self.open("?since=%d&sid=%s" % (last, sid))
        self.assertEqual(self.read_event(r)["event"], "hello")
        got = [self.read_event(r) for _ in range(3)]
        self.assertEqual([(g["event"], json.loads(g["data"]).get("key")) for g in got],
                         [("notify", "chat:carlos"), ("notify", "chat:ana"), ("clear", "chat:carlos")])
        self.assertEqual([int(g["id"]) for g in got], [last + 1, last + 2, last + 3])
        c.close()

    def test_idle_stream_pings_and_strangers_are_refused(self):
        c, r = self.open("?since=0&sid=x")
        self.read_event(r); self.read_event(r)
        self.assertEqual(r.fp.readline(), b": ping\n", "keeps the connection (and the phone's NAT) alive")
        c.close()
        c, r = self.open(login="")
        self.assertEqual(r.status, 403)
        c.close()


if __name__ == "__main__":
    unittest.main()
