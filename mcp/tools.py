# Notifications tools: the agent sends the owner a notification and reads recent ones.
# Everything goes to one topic on the box's own ntfy server (the engine behind this front), which the owner
# subscribes to once in the ntfy phone app or the web app. Nothing leaves the box unless the owner points
# their phone at it.

TOPIC = os.environ.get("NOTIFY_TOPIC", "wizard")
PRIORITIES = {"min": 1, "low": 2, "default": 3, "high": 4, "urgent": 5}


def _box_only():
    """Only apps on this box (e.g. the agent through the Wizard hub) can use these tools, never a request that
    came in through Tailscale Serve, a tunnel or a proxy."""
    h = REQUEST.get()
    if any(h.get(k) for k in ("tailscale-user-login", "x-forwarded-for", "forwarded", "cf-connecting-ip",
                              "cf-access-jwt-assertion", "x-real-ip")):
        raise PermissionError("the Notifications tools are only for apps on this box")


def _clean(text, limit):
    text = " ".join(str(text or "").split()) if limit <= 250 else str(text or "").strip()
    return text[:limit]


@tool("notify", "Send the owner a notification on their phone and in the Notifications app. Use it for things "
      "they asked to be told about, finished long jobs, or problems that need them. Keep it short and plain.",
      {"message": {"type": "string", "description": "What happened, in one or two sentences"},
       "title": {"type": "string", "description": "Short title, e.g. 'Video ready' (optional)"},
       "priority": {"type": "string", "enum": list(PRIORITIES), "description": "default unless it is urgent"},
       "link": {"type": "string", "description": "https link to open when tapped (optional)"},
       "tags": {"type": "string", "description": "Comma-separated emoji short codes, e.g. 'white_check_mark' (optional)"}},
      ["message"])
def notify(message, title="", priority="default", link="", tags=""):
    _box_only()
    body = _clean(message, 4000)
    if not body:
        raise ValueError("say what to tell the owner")
    payload = {"topic": TOPIC, "message": body, "priority": PRIORITIES.get(str(priority or "default"), 3)}
    if title:
        payload["title"] = _clean(title, 250)
    if link:
        url = str(link).strip()
        if not url.startswith("https://") or any(c.isspace() for c in url):
            raise ValueError("link must be an https:// address")
        payload["click"] = url[:2000]
    if tags:
        payload["tags"] = [t for t in (_clean(x, 40) for x in str(tags).split(",")) if t][:5]
    status, raw = http("POST", "/", json.dumps(payload).encode(), {"Content-Type": "application/json"}, timeout=20)
    sent = json.loads(raw or b"{}")
    return {"sent": True, "id": sent.get("id"), "topic": TOPIC, "time": sent.get("time")}


@tool("notifications_recent", "Read the notifications sent to the owner recently, newest last.",
      {"since": {"type": "string", "description": "How far back: e.g. '30m', '12h', '2d' (default 24h)"},
       "limit": {"type": "integer", "description": "Max notifications, default 20, up to 100"}})
def notifications_recent(since="24h", limit=20):
    _box_only()
    window = str(since or "24h").strip()
    if not window or len(window) > 6 or not window[:-1].isdigit() or window[-1] not in "smhd":
        raise ValueError("since looks like 30m, 12h or 2d")
    cap = max(1, min(int(limit or 20), 100))
    status, raw = http("GET", "/%s/json?poll=1&since=%s" % (urllib.parse.quote(TOPIC), window), timeout=20)
    items = []
    for line in raw.decode("utf-8", "replace").splitlines():
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("event") != "message":
            continue
        items.append({"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(m.get("time", 0))),
                      "title": m.get("title", ""), "message": m.get("message", ""),
                      "priority": m.get("priority", 3), "link": m.get("click", "")})
    return {"topic": TOPIC, "since": window, "count": len(items), "notifications": items[-cap:]}
