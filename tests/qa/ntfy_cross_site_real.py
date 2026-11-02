# Usage: PW=<playwright-core> [CHROME=<chromium>] python3 tests/qa/ntfy_cross_site_real.py . <base sha>
"""Base vs candidate Notifications front over a real ntfy v2.29.0 scratch container; real Chromium as the hostile page.
Prints what the hostile page got and which messages actually landed in ntfy. Synthetic messages only."""
import urllib.error, json, os, socket, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

REPO = Path(sys.argv[1]); BASE = sys.argv[2]
QA = Path(__file__).parent
NTFY = "binwiederhier/ntfy:v2.29.0@sha256:4c599cf0818965782f93f652bd843e92714992e84d6b88ad17713dc0951cda1f"


def free():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]


def wait(port):
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close(); return
        except OSError:
            time.sleep(0.1)
    raise SystemExit("port %d never opened" % port)


def source(rev):
    files = ("front.py", "tools.py", "minimal_assets.py", "relay.py")
    get = (lambda f: subprocess.check_output(["git", "-C", str(REPO), "show", "%s:mcp/%s" % (rev, f)], text=True)) if rev else \
          (lambda f: (REPO / "mcp" / f).read_text())
    return "\n\n".join(get(f) for f in files) + '\n\nif __name__ == "__main__":\n    asyncio.run(main())\n'


np_ = free()
name = "ntfy-bt386-%d" % np_
subprocess.run(["docker", "run", "-d", "--rm", "--name", name, "--network", "host", "--read-only", "--tmpfs", "/tmp",
                "-e", "NTFY_LISTEN_HTTP=127.0.0.1:%d" % np_, "-e", "NTFY_ENABLE_LOGIN=false", NTFY, "serve"],
               check=True, stdout=subprocess.DEVNULL)
try:
    wait(np_)
    for label, rev in (("base " + BASE[:8], BASE), ("candidate (working tree)", None)):
        with tempfile.TemporaryDirectory() as tmp:
            fp = free(); f = Path(tmp) / "front.py"; f.write_text(source(rev))
            env = {**{k: v for k, v in os.environ.items() if not k.startswith(("WIZARD_", "MCP_"))},
                   "WIZARD_APP": "notifications", "FRONT_PORT": str(fp), "UPSTREAM_HOST": "127.0.0.1", "UPSTREAM_PORT": str(np_),
                   "INBOX_TOPICS": "wizard", "INBOX_STATE": str(Path(tmp) / "inbox.json"), "CRON_RELAY": "off"}
            proc = subprocess.Popen([sys.executable, str(f)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                wait(fp)
                urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:%d/wizard" % fp, data=b"synthetic owner note", method="POST"), timeout=10).read()
                try:
                    hdr = urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:%d/wizard/json?poll=1" % fp,
                                                 headers={"Origin": "https://evil.example"}), timeout=10)
                    print(label, "Origin evil -> %d ACAO=%s" % (hdr.status, hdr.headers.get("Access-Control-Allow-Origin")))
                except urllib.error.HTTPError as e:
                    print(label, "Origin evil -> %d ACAO=%s" % (e.code, e.headers.get("Access-Control-Allow-Origin")))
                out = subprocess.run(["node", str(QA / "ntfy-cross-site.cjs"), str(fp), str(free())], capture_output=True, text=True,
                                     timeout=120, env={**os.environ, "PW": os.environ["PW"]})
                print(label, "hostile page got:", out.stdout.strip() or out.stderr.strip()[-400:])
                time.sleep(0.5)
                raw = urllib.request.urlopen("http://127.0.0.1:%d/wizard/json?poll=1&since=all" % np_, timeout=10).read().decode()
                msgs = [json.loads(l)["message"] for l in raw.splitlines() if l.strip()]
                print(label, "messages in ntfy:", msgs)
            finally:
                proc.terminate(); proc.wait(10)
        subprocess.run(["docker", "restart", name], check=True, stdout=subprocess.DEVNULL); wait(np_)
finally:
    subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
