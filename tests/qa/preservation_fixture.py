#!/usr/bin/env python3
"""Synthetic-only source fixture. No upstream, owner paths, production network or Docker support."""
import argparse
import json
import mimetypes
from pathlib import Path
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests'))
from test_preservation_integration import load, DONE

HOST = b'''<!doctype html><html data-skin="obsidian"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/wizard-os-home/core/skins.css"><script src="/wizard-os-home/core/skins.js"></script><script src="/fixture.js" defer></script><script src="/wizard-os-home/core/appearance-host.js" defer></script>
<style>html,body{margin:0;height:100%;background:var(--bg);color:var(--ink);font-family:system-ui}body{display:flex;flex-direction:column}header{padding:8px;flex:none}button{padding:8px;margin:2px}.win{flex:1;min-height:0;display:flex;background:var(--bg)}iframe{border:0;width:100%;height:100%}</style></head>
<body><header>Synthetic Notifications - no real accounts or messages<br><button data-skinpick="obsidian">Obsidian</button><button data-skinpick="obsidian-light">Obsidian Light</button></header><section class="win"><iframe src="/notifications/"></iframe></section></body></html>'''
JS = b'''window.WizCore={apps:()=>({APPS:[{id:'notifications',url:'/notifications/'}]})};
addEventListener('DOMContentLoaded',()=>{WosSkin.set('obsidian');document.querySelectorAll('[data-skinpick]').forEach(b=>b.onclick=()=>WosSkin.set(b.dataset.skinpick));});'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--shell-web', type=Path, required=True)
    args = parser.parse_args()
    shell = args.shell_web.resolve()
    assert (shell / 'core/app-client.js').is_file()
    with tempfile.TemporaryDirectory() as tmp:
        ns = load(Path(tmp))
        for title, message in [('Backup check', 'Synthetic original first run'), ('Recovered: Backup check', 'Synthetic original second run')]:
            ns['rewrite_and_deliver']({'topic': 'cron', 'title': title, 'message': message, 'tags': ['cron', 'app:backup']},
                rewrite=lambda p: ({'verdict': 'fyi', 'title': 'Readable backup update', 'body': 'The backup finished. Nothing to do.'}, DONE))
        note = ns['rewrite_and_deliver']({'topic': 'wizard', 'title': 'Fixture FYI', 'message': 'Synthetic original FYI details'},
            rewrite=lambda p: ({'verdict': 'fyi', 'title': 'Fixture FYI', 'body': 'The fixture check completed. Nothing to do.'}, DONE))
        messages = [{'event': 'message', 'id': 'fixture-needs-you', 'title': 'Fixture needs you', 'message': 'Choose a time for the synthetic check.', 'time': 2000000000, 'priority': 4}]
        ns['http'] = lambda method, path, **kw: (200, ('\n'.join(json.dumps(m) for m in messages) if path.startswith('/wizard/') else '').encode())

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def reply(self, code, ctype, body):
                self.send_response(code)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-src 'self'; frame-ancestors 'self'")
                self.end_headers(); self.wfile.write(body)
            def do_GET(self):
                path = urlsplit(self.path).path
                if path == '/fixture.html': return self.reply(200, 'text/html; charset=utf-8', HOST)
                if path == '/fixture.js': return self.reply(200, 'text/javascript', JS)
                if path.startswith('/wizard-os-home/'):
                    file = (shell / path.removeprefix('/wizard-os-home/')).resolve()
                    if not file.is_relative_to(shell) or not file.is_file(): return self.reply(404, 'text/plain', b'Not found')
                    return self.reply(200, mimetypes.guess_type(file.name)[0] or 'application/octet-stream', file.read_bytes())
                if path == '/fixture/marks':
                    return self.reply(200, 'application/json', json.dumps(ns['_load_state']()).encode())
                if path.startswith('/notifications/'):
                    code, ctype, data = ns['inbox_route']('GET', path, urlsplit(self.path).query, {}, b'')
                    return self.reply(code, ctype, data)
                return self.reply(404, 'text/plain', b'Unknown fixture route')
            def do_POST(self):
                # Only this one real app seam may mutate this fixture's temporary synthetic state.
                if self.path != '/notifications/api/mark': return self.reply(405, 'text/plain', b'No external actions')
                size = int(self.headers.get('Content-Length', '0'))
                if size > 8192: return self.reply(413, 'text/plain', b'Too large')
                code, ctype, data = ns['inbox_route']('POST', self.path, '', {'content-type': 'application/json'}, self.rfile.read(size))
                return self.reply(code, ctype, data)
            do_PUT = do_DELETE = do_PATCH = do_POST

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        print(json.dumps({'origin': 'http://127.0.0.1:' + str(server.server_port), 'synthetic': True}), flush=True)
        try: server.serve_forever()
        finally: server.server_close()


if __name__ == '__main__': main()
