"""ntfy on this app's own port answers every request with Access-Control-Allow-Origin: * and has no login, so a web
page on another site (or another app's port on this box) could read and publish the owner's notifications from the
owner's browser. The real front process refuses those browser requests before they reach ntfy; the ntfy web app
(same origin), the phone apps and box scripts (no browser headers) and a link that opens the web app still work.
Without MCP_TOKEN the /mcp handshake is box-only like the tools. Synthetic data, stub ntfy (records each request)."""
import http.client, http.server, json, os, socket, subprocess, sys, tempfile, threading, time, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = "box.example.ts.net:8790"
HITS = []


class Ntfy(http.server.BaseHTTPRequestHandler):
    def reply(self):
        HITS.append((self.command, self.path))
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        body = b'{"id":"Synth1","event":"message","topic":"wizard","message":"synthetic"}\n'
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")  # what ntfy v2.29 sends on every answer
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PUT = do_OPTIONS = do_HEAD = reply

    def log_message(self, format, *args):
        pass


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def front_source():
    src = "\n\n".join((ROOT / "mcp" / f).read_text() for f in ("front.py", "tools.py", "minimal_assets.py", "relay.py"))
    return src + '\n\nif __name__ == "__main__":\n    asyncio.run(main())\n'


class Front:
    def __init__(self, tmp, up, **env):
        self.port = free_port()
        f = Path(tmp) / ("front-%d.py" % self.port)
        f.write_text(front_source())
        e = {k: v for k, v in os.environ.items() if not k.startswith(("WIZARD_", "MCP_"))}
        e.update(WIZARD_APP="notifications", FRONT_PORT=str(self.port), UPSTREAM_HOST="127.0.0.1", UPSTREAM_PORT=str(up),
                 INBOX_TOPICS="wizard,cron", INBOX_STATE=str(Path(tmp) / "inbox.json"), CRON_RELAY="off",
                 PYTHONDONTWRITEBYTECODE="1", **env)
        self.proc = subprocess.Popen([sys.executable, str(f)], env=e, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", self.port), 0.2).close()
                return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("front did not start")

    def stop(self):
        self.proc.terminate()
        self.proc.communicate(timeout=10)

    def req(self, method, path, headers=(), body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        c.putheader("Host", HOST)
        for k, v in headers:
            c.putheader(k, v)
        if body is not None:
            c.putheader("Content-Length", str(len(body)))
        c.endheaders(body)
        r = c.getresponse()
        data, hdrs = r.read(), {k.lower(): v for k, v in r.getheaders()}
        c.close()
        return r.status, hdrs, data


EVIL = ("Origin", "https://evil.example")
SHELL = ("Origin", "https://box.example.ts.net:8444")   # another port on the same box: same-site, cross-origin
SELF = ("Origin", "https://" + HOST)
SERVE = [("Tailscale-User-Login", "owner@example.com"), ("X-Forwarded-For", "100.64.0.2"), ("X-Forwarded-Host", HOST)]


class CrossSite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ntfy = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Ntfy)
        threading.Thread(target=cls.ntfy.serve_forever, daemon=True).start()
        cls.front = Front(cls.tmp.name, cls.ntfy.server_address[1])

    @classmethod
    def tearDownClass(cls):
        cls.front.stop()
        cls.ntfy.shutdown()
        cls.tmp.cleanup()

    def refused(self, method, path, headers, body=None):
        before = len(HITS)
        code, hdrs, data = self.front.req(method, path, headers, body)
        self.assertEqual(code, 403, (method, path, headers, data))
        self.assertFalse([k for k in hdrs if k.startswith("access-control-")], hdrs)
        self.assertEqual(len(HITS), before, "a refused request never reaches ntfy")

    def passed(self, method, path, headers, body=None):
        before = len(HITS)
        code, _, data = self.front.req(method, path, headers, body)
        self.assertEqual(code, 200, (method, path, headers, data))
        self.assertEqual(HITS[before:], [(method, path)])

    def test_other_sites_cannot_read_or_publish(self):
        cors_get = [EVIL, ("Sec-Fetch-Site", "cross-site"), ("Sec-Fetch-Mode", "cors"), ("Sec-Fetch-Dest", "empty")]
        self.refused("GET", "/wizard/json?poll=1&since=all", SERVE + cors_get)
        self.refused("POST", "/wizard", SERVE + [EVIL, ("Content-Type", "text/plain")], b"phish")
        self.refused("PUT", "/wizard", SERVE + [EVIL], b"phish")
        self.refused("OPTIONS", "/wizard", SERVE + [EVIL, ("Access-Control-Request-Method", "POST")])
        self.refused("GET", "/wizard/ws", SERVE + [EVIL, ("Upgrade", "websocket"), ("Connection", "Upgrade")])
        self.refused("GET", "/wizard/json", SERVE + [("Origin", "null")])            # sandboxed frame, data: page
        self.refused("GET", "/wizard/json", SERVE + [SHELL, ("Sec-Fetch-Site", "same-site")])  # another app's port
        # <img src=".../wizard/publish?message=..."> publishes with GET: no Origin, but Fetch Metadata or Referer.
        img = [("Sec-Fetch-Site", "cross-site"), ("Sec-Fetch-Mode", "no-cors"), ("Sec-Fetch-Dest", "image")]
        self.refused("GET", "/wizard/publish?message=phish", SERVE + img)
        self.refused("GET", "/wizard/send?message=phish", SERVE + [("Referer", "https://evil.example/page")])
        nav = [("Sec-Fetch-Site", "cross-site"), ("Sec-Fetch-Mode", "navigate"), ("Sec-Fetch-Dest", "document")]
        self.refused("GET", "/wizard/trigger", SERVE + nav)                       # a link can't publish either
        self.refused("GET", "/wizard/json", SERVE + [("Sec-Fetch-Site", "cross-site"), ("Sec-Fetch-Mode", "navigate"),
                                                      ("Sec-Fetch-Dest", "iframe")])  # framing the web app

    def test_web_app_phone_apps_and_box_scripts_still_work(self):
        same = [("Sec-Fetch-Site", "same-origin"), ("Sec-Fetch-Mode", "cors"), ("Sec-Fetch-Dest", "empty")]
        self.passed("GET", "/wizard/json?poll=1", SERVE + same)                   # ntfy web app reading
        self.passed("POST", "/wizard", SERVE + same + [SELF], b"hello")             # ntfy web app publishing
        self.passed("GET", "/wizard/json?poll=1", SERVE)                           # phone app over Tailscale
        self.passed("POST", "/wizard", [], b"done")                                 # box script, curl
        self.passed("GET", "/wizard/publish?message=done", [])                     # box script, GET publish
        self.passed("GET", "/", SERVE + [("Sec-Fetch-Site", "cross-site"), ("Sec-Fetch-Mode", "navigate"),
                                         ("Sec-Fetch-Dest", "document"), ("Referer", "https://mail.example/")])  # a link
        self.passed("GET", "/wizard/publish?message=x", SERVE + [("Referer", "https://%s/" % HOST)])

    def test_inbox_routes_are_unchanged(self):
        # The Notifications view and the shell bell (/notifications/*) are app routes, not ntfy: not this gate's job.
        code, _, _ = self.front.req("GET", "/notifications/api/count", SERVE + [("Origin", "null")])
        self.assertNotEqual(code, 403)

    def test_mcp_handshake_is_box_only_without_a_token(self):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
        ctype = ("Content-Type", "application/json")
        for h in (SERVE, [("X-Forwarded-For", "100.64.0.9")], [("Forwarded", "for=100.64.0.9")], [("X-Real-IP", "1.2.3.4")],
                  [("CF-Connecting-IP", "1.2.3.4")]):
            code, _, data = self.front.req("POST", "/mcp", h + [ctype], body)
            self.assertEqual(code, 403, (h, data))
            self.assertNotIn(b"notify", data)
        code, _, data = self.front.req("POST", "/mcp", [EVIL, ctype], body)
        self.assertEqual(code, 403)
        code, _, data = self.front.req("POST", "/mcp", [ctype], body)            # the agent through the Wizard hub
        self.assertEqual(code, 200)
        self.assertIn("notify", [t["name"] for t in json.loads(data)["result"]["tools"]])


class WithToken(unittest.TestCase):
    def test_token_is_still_the_authority_when_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            ntfy = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Ntfy)
            threading.Thread(target=ntfy.serve_forever, daemon=True).start()
            front = Front(tmp, ntfy.server_address[1], MCP_TOKEN="synthetic-test-token-0123456789")
            try:
                body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}).encode()
                code, _, _ = front.req("POST", "/mcp", [("Content-Type", "application/json")], body)
                self.assertEqual(code, 401)
                auth = ("Authorization", "Bearer synthetic-test-token-0123456789")
                code, _, _ = front.req("POST", "/mcp", [auth, ("X-Forwarded-For", "100.64.0.2"), ("Content-Type", "application/json")], body)
                self.assertEqual(code, 200)
            finally:
                front.stop()
                ntfy.shutdown()


if __name__ == "__main__":
    unittest.main()
