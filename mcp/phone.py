

# ---- Phone: instant per-app notifications on the owner's phone, without Google ----
# Apps on this box send their inbox feed (the Wizard OS inbox feed v1 shape: one item per thing that needs the
# owner) to POST /notifications/api/app-feed whenever it changes. The app is known by the Docker link network its
# request comes from (wizard-link-<app>--notifications, made by the App Manager for a declared `uses: [notifications]`),
# never by anything in the request. The router compares the feed with the last one and, for apps routed to Chats
# (api/routes, the same choice Settings › Notifications saves), records numbered events: notify (new or newer
# activity), clear (the thing left the feed: read somewhere else). Badge apps only get a count, Morning report / Off
# apps nothing. Text of apps not routed to Chats is never stored.
#
# The Wizard OS Android app keeps ONE connection open to GET /notifications/api/phone/stream (Server-Sent Events,
# through the box's private Tailscale address) and posts real Android notifications. On reconnect it sends the last
# event id it saw (?since=<id>&sid=<server id>): it gets every event after it, or a full `reset` snapshot when the
# id is too old or the router's store is new. GET /notifications/api/phone/events is the same catch-up as one JSON
# answer (the phone's backstop job). Readers must come through Tailscale Serve with a user identity
# (Tailscale-User-Login; PHONE_OWNER_LOGIN pins it to the owner) and never from another app's container.
import re
import socket
import sqlite3

PHONE_DB = os.environ.get("PHONE_DB", os.path.join(os.path.dirname(STATE_FILE) or "/data", "phone.sqlite3"))
PHONE_OWNER = os.environ.get("PHONE_OWNER_LOGIN", "").strip().lower()
PHONE_KEEP_S = 7 * 86400          # events are kept a week (and at most PHONE_KEEP_N); older cursors get a reset
PHONE_KEEP_N = 5000
PHONE_PING_S = float(os.environ.get("PHONE_PING_S", "25") or 25)
PHONE_STREAMS_MAX = 8
PHONE_ITEMS_MAX = 200
PHONE_ACTIONS_MAX = 4
PHONE_FEED = INBOX + "api/app-feed"
PHONE_STREAM = INBOX + "api/phone/stream"
PHONE_EVENTS = INBOX + "api/phone/events"
PHONE_INFO = INBOX + "api/phone"
_phone_lock = threading.Lock()
_phone_wake = []                  # (loop, asyncio.Event) of each open stream; set after every new event
_phone_streams = []               # stop flags of open streams, oldest first
_peer_cache = {}


# -- who is asking --

def peer_name(ip):
    """The Docker DNS name of a container peer (`<container>.<network>`), or "" for anything else (the host)."""
    hit = _peer_cache.get(ip)
    if hit and time.time() - hit[1] < 60:
        return hit[0]
    try:
        name = socket.gethostbyaddr(ip)[0]
    except (OSError, UnicodeError):
        name = ""
    if name == ip or not re.search(r"[a-z]", name or ""):
        name = ""
    _peer_cache[ip] = (name, time.time())
    return name


def peer_app(ip):
    """The app a box-local request comes from: only through its link network to this app (Docker names it, the
    caller can't). None for the host, the tailnet or a container on any other network."""
    name = peer_name(ip) if ip else ""
    net = name.partition(".")[2]
    m = re.fullmatch(r"wizard-link-([a-z0-9][a-z0-9-]{0,63})--notifications", net)
    app = m.group(1) if m else None
    return app if app and app != "notifications" and _source_app(app) else None


def phone_reader_ok(headers):
    """The phone stream: through Tailscale Serve (it sets the user identity and replaces any sent one), not from
    another app's container on the box (which could send any header)."""
    if peer_name(headers.get(":peer", "")):
        return False
    login = headers.get("tailscale-user-login", "").strip().lower()
    if not login:
        return False
    return not PHONE_OWNER or login == PHONE_OWNER


# -- store --

def _db():
    db = sqlite3.connect(PHONE_DB, timeout=10)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
        CREATE TABLE IF NOT EXISTS items (app TEXT, key TEXT, time INTEGER, hash TEXT, data TEXT, shown INTEGER,
                                          PRIMARY KEY (app, key));
        CREATE TABLE IF NOT EXISTS counts (app TEXT PRIMARY KEY, n INTEGER);
        CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL, data TEXT);
    """)
    if not db.execute("SELECT v FROM meta WHERE k='sid'").fetchone():
        db.execute("INSERT INTO meta VALUES ('sid', ?)", (uuid.uuid4().hex[:16],))
        db.commit()
    return db


def phone_route(app):
    r = inbox_routes()
    return {**r["defaults"], **r["routes"]}.get(app, "badge")   # an app nobody picked is a Badge (shell default)


def _txt(v, limit):
    return " ".join(str(v or "").split())[:limit] if isinstance(v, (str, int, float)) else ""


def clean_feed_items(items):
    if not isinstance(items, list):
        raise ValueError("send the feed as {inbox: 1, items: [...]}")
    out = {}
    for it in items[:PHONE_ITEMS_MAX]:
        if not isinstance(it, dict):
            continue
        key = it.get("key")
        if not isinstance(key, str) or not 0 < len(key) <= 200:
            continue
        try:
            t = int(it.get("time") or 0)
            pr = max(1, min(int(it.get("priority") or 3), 5))
        except (TypeError, ValueError):
            continue
        acts = []
        for a in (it.get("actions") or [])[:PHONE_ACTIONS_MAX] if isinstance(it.get("actions"), list) else []:
            # The phone can run an app action (posted back to the app's own api/inbox/act) or take typed words
            # (input). An `ask` goes to the agent, which the phone can't do from a notification: left out.
            if not isinstance(a, dict) or a.get("ask") or not re.fullmatch(r"[a-z0-9_-]{1,40}", str(a.get("id") or "")):
                continue
            acts.append({"id": a["id"], "label": _txt(a.get("label"), 40) or a["id"], **({"input": True} if a.get("input") is True else {})})
        out[key] = {"key": key, "title": _txt(it.get("title"), 200), "text": str(it.get("text") or "").strip()[:2000],
                    "time": t, "priority": pr, "actions": acts}
    return out


def _event(db, ev, now):
    db.execute("INSERT INTO events (at, data) VALUES (?, ?)", (now, json.dumps(ev, separators=(",", ":"))))


def _prune(db, now):
    top = db.execute("SELECT MAX(id) FROM events").fetchone()[0] or 0
    db.execute("DELETE FROM events WHERE at < ? OR id <= ?", (now - PHONE_KEEP_S, top - PHONE_KEEP_N))


def _wake():
    for loop, ev in list(_phone_wake):
        try:
            loop.call_soon_threadsafe(ev.set)
        except RuntimeError:
            pass


def ingest_feed(app, feed, now=None):
    """One app's whole current feed. Returns how many phone events it made."""
    if not _source_app(app):
        raise ValueError("unknown app")
    if not isinstance(feed, dict) or feed.get("inbox") != 1:
        raise ValueError("send the feed as {inbox: 1, items: [...]}")
    items = clean_feed_items(feed.get("items"))
    now = now or time.time()
    route = phone_route(app)
    made = 0
    with _phone_lock:
        db = _db()
        try:
            prev = {k: (t, h, s) for k, t, h, s in db.execute("SELECT key, time, hash, shown FROM items WHERE app=?", (app,))}
            # The first feed an app ever sends is its backlog (every chat already unread): shown quietly, no buzz each.
            first = db.execute("SELECT 1 FROM meta WHERE k=?", ("seen:" + app,)).fetchone() is None
            if first:
                db.execute("INSERT INTO meta VALUES (?, ?)", ("seen:" + app, str(int(now))))
            for key, it in items.items():
                h = hashlib.sha256(json.dumps(it, sort_keys=True).encode()).hexdigest()[:20]
                old = prev.get(key)
                shown = bool(old and old[2])
                if route == "chats" and (old is None or old[1] != h):
                    # Newer activity alerts; the same activity re-worded (a translation, a name) updates quietly.
                    alert = not first and (old is None or it["time"] > old[0])
                    _event(db, dict(it, type="notify", app=app, alert=alert), now)
                    made += 1
                    shown = True
                db.execute("INSERT OR REPLACE INTO items VALUES (?, ?, ?, ?, ?, ?)",
                           (app, key, it["time"], h, json.dumps(it) if route == "chats" else None, int(shown)))
            for key, (_, _, shown) in prev.items():
                if key not in items:
                    if shown:
                        _event(db, {"type": "clear", "app": app, "key": key}, now)
                        made += 1
                    db.execute("DELETE FROM items WHERE app=? AND key=?", (app, key))
            n = len(items) if route == "badge" else 0
            row = db.execute("SELECT n FROM counts WHERE app=?", (app,)).fetchone()
            if (row[0] if row else 0) != n:
                db.execute("INSERT OR REPLACE INTO counts VALUES (?, ?)", (app, n))
                _event(db, {"type": "count", "app": app, "count": n}, now)
                made += 1
            if made:
                _prune(db, now)
            db.commit()
        finally:
            db.close()
    if made:
        _wake()
    return made


def phone_routes_changed(changes, now=None):
    """Settings moved an app: one that left Chats takes its phone notifications away and forgets their text."""
    now = now or time.time()
    made = 0
    with _phone_lock:
        db = _db()
        try:
            for app, to in (changes or {}).items():
                if to == "chats" or not _source_app(app):
                    continue
                for (key,) in db.execute("SELECT key FROM items WHERE app=? AND shown=1", (app,)).fetchall():
                    _event(db, {"type": "clear", "app": app, "key": key}, now)
                    made += 1
                db.execute("UPDATE items SET data=NULL, shown=0 WHERE app=?", (app,))
            if made:
                _prune(db, now)
            db.commit()
        finally:
            db.close()
    if made:
        _wake()


ROUTE_HOOKS.append(phone_routes_changed)


def phone_snapshot(db):
    items = []
    for app, data in db.execute("SELECT app, data FROM items WHERE data IS NOT NULL ORDER BY time"):
        if phone_route(app) == "chats":
            items.append(dict(json.loads(data), app=app))
    counts = {a: n for a, n in db.execute("SELECT app, n FROM counts WHERE n > 0")}
    return {"items": items, "counts": counts}


def phone_since(since, sid, limit=500):
    """Catch-up for a reader: {sid, latest, reset?, items?, counts?, events:[(id, event)]}."""
    db = _db()
    try:
        mine = db.execute("SELECT v FROM meta WHERE k='sid'").fetchone()[0]
        latest = db.execute("SELECT MAX(id) FROM events").fetchone()[0] or 0
        oldest = db.execute("SELECT MIN(id) FROM events").fetchone()[0] or latest + 1
        try:
            since = int(since)
        except (TypeError, ValueError):
            since = -1
        out = {"v": 1, "sid": mine, "latest": latest}
        if sid != mine or since < 0 or since > latest or (since < oldest - 1):
            out.update(phone_snapshot(db), reset=True, events=[])
            return out
        rows = db.execute("SELECT id, data FROM events WHERE id > ? ORDER BY id LIMIT ?", (since, limit)).fetchall()
        out["events"] = [(i, json.loads(d)) for i, d in rows]
        return out
    finally:
        db.close()


def phone_after(last, limit=200):
    db = _db()
    try:
        return [(i, json.loads(d)) for i, d in db.execute("SELECT id, data FROM events WHERE id > ? ORDER BY id LIMIT ?", (last, limit))]
    finally:
        db.close()


@route(PHONE_FEED)
def phone_feed_route(method, path, query, headers, body):
    if path != PHONE_FEED:
        return _json(404, {"error": "not found"})
    if method != "POST":
        return _json(405, {"error": "POST your inbox feed"})
    if networked(headers):
        return _json(403, {"error": "only apps on this box send their feed"})
    app = peer_app(headers.get(":peer", ""))
    if not app:
        return _json(403, {"error": "declare uses: [notifications] in wizard.md to send your feed"})
    if not headers.get("content-type", "").startswith("application/json"):
        return _json(415, {"error": "send application/json"})
    try:
        feed = json.loads(body or b"{}")
        return _json(200, {"ok": True, "app": app, "events": ingest_feed(app, feed), "route": phone_route(app)})
    except ValueError as e:
        return _json(400, {"error": str(e)})


@route(PHONE_INFO)
def phone_info_route(method, path, query, headers, body):
    if path == PHONE_EVENTS:
        if method != "GET":
            return _json(405, {"error": "use GET"})
        if not phone_reader_ok(headers):
            return _json(403, {"error": "open it from the owner's own device"})
        q = urllib.parse.parse_qs(query)
        out = phone_since((q.get("since") or [""])[0], (q.get("sid") or [""])[0])
        out["events"] = [dict(e, id=i) for i, e in out["events"]]
        return _json(200, out)
    if path in (PHONE_INFO, PHONE_INFO + "/") and method == "GET":
        return _json(200, {"phone": 1, "stream": "api/phone/stream", "events": "api/phone/events"})
    return _json(404, {"error": "not found"})


def _sse(i, ev):
    head = "id: %d\n" % i if i is not None else ""
    return (head + "event: %s\ndata: %s\n\n" % (ev.get("type", "message"), json.dumps(ev, separators=(",", ":"), ensure_ascii=False))).encode()


@stream(PHONE_STREAM)
async def phone_stream(reader, writer, method, target, rest):
    headers = parse_headers(rest)
    headers[":peer"] = (writer.get_extra_info("peername") or ("",))[0]
    if method != "GET":
        return await respond(writer, 405, {"error": "use GET"})
    if not await asyncio.to_thread(phone_reader_ok, headers):
        return await respond(writer, 403, {"error": "open it from the owner's own device"})
    q = urllib.parse.parse_qs(target.partition("?")[2])
    since = (q.get("since") or [headers.get("last-event-id", "")])[0]
    first = await asyncio.to_thread(phone_since, since, (q.get("sid") or [""])[0])
    stop = asyncio.Event()
    _phone_streams.append(stop)
    while len(_phone_streams) > PHONE_STREAMS_MAX:      # a phone that reconnects never leaves a ghost behind
        _phone_streams.pop(0).set()
    wake = (asyncio.get_running_loop(), asyncio.Event())
    _phone_wake.append(wake)
    gone = asyncio.ensure_future(reader.read(1))        # the phone hung up
    try:
        writer.write(("HTTP/1.1 200 OK\r\nContent-Type: text/event-stream; charset=utf-8\r\nCache-Control: no-store\r\n"
                      "X-Accel-Buffering: no\r\nX-Content-Type-Options: nosniff\r\nConnection: close\r\n\r\nretry: 5000\n\n").encode())
        writer.write(_sse(None, {"type": "hello", "v": 1, "sid": first["sid"], "latest": first["latest"]}))
        last = first["latest"] if first.get("reset") else int(since)
        if first.get("reset"):
            writer.write(_sse(first["latest"], {"type": "reset", "items": first["items"], "counts": first["counts"]}))
        for i, ev in first["events"]:
            writer.write(_sse(i, ev))
            last = i
        await writer.drain()
        while not stop.is_set() and not gone.done():
            wake[1].clear()
            evs = await asyncio.to_thread(phone_after, last)
            for i, ev in evs:
                writer.write(_sse(i, ev))
                last = i
            if evs:
                await writer.drain()
                continue
            waits = [asyncio.ensure_future(wake[1].wait()), asyncio.ensure_future(stop.wait())]
            done, _ = await asyncio.wait(waits + [gone], timeout=PHONE_PING_S, return_when=asyncio.FIRST_COMPLETED)
            for t in waits:
                t.cancel()
            if not done:
                writer.write(b": ping\n\n")
                await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        if wake in _phone_wake:
            _phone_wake.remove(wake)
        if stop in _phone_streams:
            _phone_streams.remove(stop)
        gone.cancel()
        try:
            writer.close()
        except Exception:
            pass
