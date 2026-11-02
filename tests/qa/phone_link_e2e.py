#!/usr/bin/env python3
"""Real-Docker check of the phone router (run on a box with Docker; optional Tailscale Serve leg).

Starts the generated front (runtipi/docker-compose.yml's command, the exact shipped code) in a throwaway container on
a link network named like the App Manager's (wizard-link-<app>--notifications), plus a second container playing the
app. Proves with real Docker DNS that:
  1. the app's POST is attributed to the app named by its link network;
  2. the host (where Tailscale Serve connects from) can read the stream with a user identity, and an event posted
     while it listens arrives at once;
  3. the app container cannot read the stream even with a forged Tailscale-User-Login header;
  4. a request without identity is refused;
  5. optional (QA_SERVE_PORT=<free tailnet port>): the same through real `tailscale serve` (SSE is not buffered and
     Serve supplies the identity). The Serve mapping is removed afterwards.
Everything is removed at the end. No owner data is read or written.
"""
import json, os, shlex, subprocess, sys, tempfile, threading, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IMG = "python:3.12-alpine@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4"
APP = os.environ.get("QA_APP", "qaphone")
NET = "wizard-link-%s--notifications" % APP
PORT = int(os.environ.get("QA_PORT", "18797"))
SERVE = os.environ.get("QA_SERVE_PORT", "")
OK = []


def sh(cmd, check=True, **kw):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, **kw)
    if check and r.returncode:
        sys.exit("FAILED: %s\n%s%s" % (cmd, r.stdout, r.stderr))
    return r.stdout.strip()


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (" — " + str(detail) if detail else ""), flush=True)
    OK.append(bool(cond))


def front_source():
    import yaml  # PyYAML: read the shipped compose exactly
    c = yaml.safe_load((ROOT / "runtipi/docker-compose.yml").read_text())
    return c["services"]["notifications"]["command"][2]


def read_sse(url, headers, want, timeout=8, on_open=None):
    req = urllib.request.Request(url, headers=headers)
    got, t0 = [], time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if on_open:
            threading.Thread(target=on_open, daemon=True).start()
        ev = {}
        for raw in r:
            line = raw.decode().rstrip("\n")
            if not line:
                if "data" in ev:
                    ev["at"] = round(time.time() - t0, 3)
                    got.append(ev)
                    if len(got) >= want:
                        return got
                ev = {}
                continue
            if line.startswith(":"):
                continue
            k, _, v = line.partition(": ")
            ev[k] = v
    return got


def main():
    tmp = tempfile.mkdtemp(prefix="phone-e2e-")
    os.chmod(tmp, 0o777)
    src = Path(tmp) / "front.py"
    src.write_text(front_source())
    try:
        sh("docker network create --internal %s" % NET)
        sh("docker run -d --rm --name qa-phone-front -p 127.0.0.1:%d:8080 -e INBOX_STATE=/data/inbox.json "
           "-e WIZARD_APP=notifications -e FRONT_PORT=8080 -e CRON_RELAY=off -e PYTHONDONTWRITEBYTECODE=1 "
           "-v %s:/data -v %s:/front.py:ro %s python /front.py" % (PORT, tmp, src, IMG))
        sh("docker network connect --alias notifications %s qa-phone-front" % NET)
        sh("docker run -d --rm --name qa-phone-app --network %s %s sleep 300" % (NET, IMG))
        for _ in range(40):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/notifications/api/phone" % PORT, timeout=1).read(); break
            except Exception:
                time.sleep(0.25)
        # Route the test app to Chats (the owner's choice, through the same API Settings uses).
        req = urllib.request.Request("http://127.0.0.1:%d/notifications/api/routes" % PORT, method="POST",
                                     data=json.dumps({"routes": {APP: "chats"}}).encode(), headers={"Content-Type": "application/json"})
        check("route saved", json.load(urllib.request.urlopen(req))["routes"].get(APP) == "chats")

        def app_post(items, extra=""):
            body = json.dumps({"inbox": 1, "items": items})
            py = ("import urllib.request,json;r=urllib.request.Request('http://notifications:8080/notifications/api/app-feed',"
                  "data=%r.encode(),headers={'Content-Type':'application/json'%s});"
                  "\ntry:\n print(urllib.request.urlopen(r).read().decode())\nexcept Exception as e:\n print(e.code, e.read().decode())") % (body, extra)
            return sh("docker exec qa-phone-app python -c %s" % shlex.quote(py))

        out = json.loads(app_post([]))
        check("app identified by its link network (Docker DNS)", out.get("app") == APP, out)
        item = {"key": "chat:qa", "title": "QA Carlos", "text": "See you at 5", "time": int(time.time()), "priority": 3,
                "actions": [{"id": "reply", "label": "Reply", "input": True}]}
        url = "http://127.0.0.1:%d/notifications/api/phone/stream" % PORT
        got = read_sse(url, {"Tailscale-User-Login": "owner@example.com"}, 3, on_open=lambda: (time.sleep(1), app_post([item])))
        check("host reader with identity: hello, reset, then the live notify", [g.get("event") for g in got] == ["hello", "reset", "notify"],
              [(g.get("event"), g["at"]) for g in got])
        check("live event arrived within 1.5 s of the app's POST", got and got[-1]["at"] < 2.6, got and got[-1]["at"])
        forged = ("import urllib.request\nr=urllib.request.Request('http://notifications:8080/notifications/api/phone/stream',"
                  "headers={'Tailscale-User-Login':'owner@example.com'})\ntry:\n urllib.request.urlopen(r,timeout=3)\n print('open')\nexcept Exception as e:\n print(getattr(e,'code',e))")
        check("app container with a forged identity header is refused", sh("docker exec qa-phone-app python -c %s" % shlex.quote(forged)) == "403")
        try:
            urllib.request.urlopen(url, timeout=3); anon = "open"
        except urllib.error.HTTPError as e:
            anon = e.code
        check("no identity is refused", anon == 403, anon)
        if SERVE:
            ip = sh("tailscale ip -4").splitlines()[0]
            host = json.loads(sh("tailscale status --json"))["Self"]["DNSName"].rstrip(".")
            sh("timeout 20 tailscale serve --bg --https=%s http://127.0.0.1:%d </dev/null" % (SERVE, PORT))
            try:
                time.sleep(2)
                sid = got[0] and json.loads(got[0]["data"])["sid"]
                last = int(got[-1]["id"])
                item2 = dict(item, text="Running late", time=item["time"] + 5)
                cmd = ("curl -sN --max-time 6 --resolve %s:%s:%s 'https://%s:%s/notifications/api/phone/stream?since=%d&sid=%s'"
                       % (host, SERVE, ip, host, SERVE, last, sid))
                p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, text=True)
                time.sleep(1.5)
                t_post = time.time(); app_post([item2])
                lines, t_seen = [], None
                for line in p.stdout:
                    lines.append(line.rstrip("\n"))
                    if line.startswith("event: notify") and t_seen is None:
                        t_seen = time.time()
                p.wait()
                check("through real Tailscale Serve: identity supplied, SSE streamed (not buffered)",
                      any(l.startswith("event: notify") for l in lines) and t_seen and t_seen - t_post < 1.5,
                      "notify %.3fs after POST" % ((t_seen or 0) - t_post))
            finally:
                sh("timeout 20 tailscale serve --https=%s off </dev/null" % SERVE, check=False)
    finally:
        sh("docker rm -f qa-phone-app qa-phone-front", check=False)
        sh("docker network rm %s" % NET, check=False)
        sh("docker run --rm -v %s:/d %s sh -c 'rm -rf /d/*'" % (tmp, IMG), check=False)
        sh("rm -rf %s" % tmp, check=False)
    print("%d/%d checks passed" % (sum(OK), len(OK)))
    sys.exit(0 if OK and all(OK) else 1)


if __name__ == "__main__":
    main()
