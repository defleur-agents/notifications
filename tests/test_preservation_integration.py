"""Live preservation + wizard26 seams. All state synthetic; unexpected external I/O fails closed."""
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import zlib
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FILES = ('front.py', 'tools.py', 'minimal_assets.py', 'relay.py', 'readable.py', 'phone.py')
DONE = {'type': 'done', 'provider': 'fixture', 'model': 'fixture', 'tier': 'fast', 'tested_on': 'fixture', 'chosen': 'fixture'}


def load(root, env=None, local=None):
    root.mkdir(exist_ok=True, parents=True)
    if local is not None:
        (root / 'cron-relay-settings.json').write_text(json.dumps({'failures': local}))
    config = {'INBOX_STATE': str(root / 'inbox.json'), 'INBOX_TOPICS': 'wizard,cron',
              'CRON_STATE': str(root / 'relay.json'), 'CRON_DIR': str(root / 'cron'),
              'READABLE_DB': str(root / 'readable.sqlite3'), 'PHONE_DB': str(root / 'phone.sqlite3'),
              'WIZARD_GRANTS_FILE': str(root / 'no-grants'), 'UPSTREAM_HOST': 'synthetic.invalid', **(env or {})}
    ns: dict[str, Any] = {'__name__': 'preservation_fixture'}
    with patch.dict(os.environ, config, clear=True):
        exec(compile('\n'.join((ROOT / 'mcp' / n).read_text() for n in FILES), 'preservation_fixture', 'exec'), ns)
    def no_http(*a, **kw):
        raise AssertionError('Unmocked external HTTP')
    ns['http'] = no_http
    return ns


class PreservationIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.root = Path(self.tmp.name)
        self.ns = load(self.root)
    def tearDown(self):
        self.tmp.cleanup()

    def dispatch(self, method, path, headers=''):
        calls = []
        ns = self.ns
        async def serve(reader, writer, fn, *args):
            calls.append(fn.__name__)
        async def response(writer, code, *args):
            calls.append(code)
        async def mcp(*args):
            calls.append('mcp')
        async def stream(*args):
            calls.append('phone_stream')
        async def connect(*args, **kw):
            raise AssertionError('Unexpected raw upstream proxy')
        async def exercise():
            reader = asyncio.StreamReader()
            reader.feed_data((method + ' ' + path + ' HTTP/1.1\r\nHost: localhost\r\n' + headers + '\r\n').encode())
            reader.feed_eof()
            with patch('asyncio.open_connection', connect):
                await ns['handle'](reader, object())
        with patch.dict(ns, {'serve_route': serve, 'respond': response, 'mcp': mcp,
                             'STREAMS': {ns['PHONE_STREAM']: stream}}):
            asyncio.run(exercise())
        return calls

    def test_specific_routes_still_win_before_guarded_generic_publish(self):
        for path, name in [('/notifications/api/app-feed', 'phone_feed_route'),
                           ('/notifications/api/phone/events', 'phone_info_route'),
                           ('/notifications/api/mark', 'inbox_route'),
                           ('/v1/readable/health', 'readable_health')]:
            with self.subTest(path=path):
                self.assertEqual(self.dispatch('POST', path), [name])
        self.assertEqual(self.dispatch('GET', '/notifications/api/phone/stream'), ['phone_stream'])
        self.assertEqual(self.dispatch('POST', '/mcp'), ['mcp'])
        self.assertEqual(self.dispatch('POST', '/mcp', 'Origin: https://evil.invalid\r\n'), [403])

    def test_old_and_new_publish_paths_enter_editor_only_after_security_guard(self):
        for method, path in [('POST', '/'), ('PUT', '/wizard'), ('POST', '/wizard'),
                             ('GET', '/wizard/publish'), ('GET', '/wizard/send'), ('GET', '/wizard/trigger')]:
            with self.subTest(method=method, path=path):
                self.assertEqual(self.dispatch(method, path), ['publish_route'])
                self.assertEqual(self.dispatch(method, path, 'Origin: https://evil.invalid\r\n'), [403])

    def test_mcp_and_cron_share_readable_durable_admission(self):
        calls = []
        self.ns['rewrite_and_deliver'] = lambda p: calls.append(p) or {'fixture': 'admitted'}
        self.ns['notify']('Finished', app='backup', tags='wave,app:spoof')
        self.assertTrue(self.ns['_cron_post']('Backup', 'Finished', 2, ['cron', 'app:backup']))
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]['tags'], ['wave', 'app:backup'])
        self.assertEqual(calls[1]['tags'], ['cron', 'app:backup'])
        self.assertEqual(calls[1]['topic'], 'cron')

    def test_quiet_originals_thread_counts_marks_and_app_marks_share_state(self):
        ns = self.ns
        for stamp, title, raw in [(100, 'Backup check', 'Synthetic original one'), (200, 'Recovered: Backup check', 'Synthetic original two')]:
            out = ns['rewrite_and_deliver']({'topic': 'cron', 'title': title, 'message': raw, 'tags': ['cron', 'app:backup']},
                rewrite=lambda p: ({'verdict': 'fyi', 'title': 'Readable update', 'body': 'Backup finished. Nothing to do.'}, DONE))
            with ns['readable_db']() as db:
                db.execute('UPDATE updates SET at=? WHERE id=?', (stamp, out['rewrite_id']))
        ns['http'] = lambda *a, **kw: (200, b'')
        row = ns['inbox_items']()['items'][0]
        self.assertEqual(len(row['runs']), 2)
        self.assertIn('Synthetic original two', row['details'])
        self.assertIn('Synthetic original two', row['runs'][0]['details'])
        self.assertIn('Synthetic original one', row['runs'][1]['details'])
        self.assertEqual(ns['inbox_items']()['counts']['unread'], 1)
        ns['inbox_mark']([row['id']], archived=True)
        self.assertEqual(ns['inbox_items']()['counts']['unread'], 0)
        self.assertEqual(len(ns['inbox_items']('archive')['items']), 1)
        ns['inbox_mark']([row['id']], archived=False, read=False)
        self.assertEqual(ns['inbox_items']()['counts']['unread'], 1)
        self.assertTrue(all(not r['read'] for r in ns['inbox_items']()['items'][0]['runs']))
        aid = 'a-messenger-0123456789abcdef'
        ns['inbox_mark']([aid], archived=True, at={aid: time.time()})
        self.assertIn(aid, ns['app_marks']()['marks'])
        self.assertEqual(ns['inbox_items']()['counts']['unread'], 1, 'app marks never add a second cron counter')

    def test_incremental_poll_keeps_cache_but_full_refresh_can_expire_it(self):
        ns = self.ns; paths = []
        def http(method, path, **kw):
            paths.append(path)
            return 200, b'{"event":"message","id":"one","time":100}\n' if len(paths) == 1 else b''
        ns['http'] = http
        with patch('time.time', return_value=1000):
            self.assertEqual(len(ns['_topic_messages']('wizard')), 1)
        with patch('time.time', return_value=1010):
            self.assertEqual(len(ns['_topic_messages']('wizard')), 1)
        self.assertIn('since=100', paths[-1])
        with patch('time.time', return_value=2000):
            self.assertEqual(ns['_topic_messages']('wizard'), [])
        self.assertIn('since=all', paths[-1])

    def test_views_explicit_classic_and_minimal_are_not_overridden(self):
        route = self.ns['inbox_route']
        for query, expected in [('', 'MINIMAL_HTML'), ('layout=cards', 'MINIMAL_HTML'), ('layout=minimal', 'MINIMAL_HTML')]:
            self.assertEqual(route('GET', '/notifications/', query, {}, b'')[2].decode(), self.ns[expected])
        classic = route('GET', '/notifications/', 'layout=classic', {}, b'')[2].decode()
        self.assertIn('layout.js', classic)
        self.assertNotEqual(classic, self.ns['MINIMAL_HTML'])
        self.ns['LAYOUT'] = 'classic'
        self.assertEqual(route('GET', '/notifications/', '', {}, b'')[2].decode(), classic)

    def test_authenticated_engine_http_never_follows_redirects_or_environment_proxy(self):
        ns = self.ns; requests = []
        password_file = self.root / 'synthetic-password'
        password_file.write_text('fixture-only-not-a-credential')
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self): return b'{}'
        class Opener:
            def open(self, req, timeout):
                requests.append(req)
                return Response()
        handlers = []
        def opener(*args):
            handlers.extend(args)
            return Opener()
        ns['up_addr'] = lambda: '127.0.0.1'
        with patch.dict(os.environ, {'UPSTREAM_PASSWORD_FILE': str(password_file)}), patch('urllib.request.build_opener', opener):
            # load() blocks I/O by replacing the function; recover only this real helper for the mock above.
            helper = {}; exec(compile((ROOT / 'mcp/front.py').read_text(), 'front_fixture', 'exec'), helper)
            helper['up_addr'] = lambda: '127.0.0.1'
            helper['http']('POST', '/', b'{}')
        self.assertTrue(requests[0].has_header('Authorization'))
        redirect = next(h for h in handlers if isinstance(h, ns['urllib'].request.HTTPRedirectHandler))
        self.assertIsNone(redirect.redirect_request(None, None, None, None, None, None))
        self.assertEqual(next(h for h in handlers if isinstance(h, ns['urllib'].request.ProxyHandler)).proxies, {})

    def test_generated_transport_decodes_exact_modules_and_is_below_linux_cap(self):
        text = (ROOT / 'runtipi/docker-compose.yml').read_text()
        block = text.split('# BEGIN MCP FRONT (generated by mcp/inline.py)\n', 1)[1].split('# END MCP FRONT', 1)[0]
        command = block.split('\n', 1)[1].strip()
        encoded = command.split('base64.b64decode(', 1)[1].split(')', 1)[0].strip("'\"")
        decoded = zlib.decompress(base64.b64decode(encoded)).decode()
        for name in FILES:
            self.assertIn((ROOT / 'mcp' / name).read_text(), decoded, name)
        self.assertLess(len(command.encode()) + 1, 131072)
        self.assertIn('asyncio.run(main())', decoded)


class FailureSelectionMatrix(unittest.TestCase):
    def test_all_selections_responder_presence_recovery_and_unacknowledged_escalation(self):
        # Explicit env wins; auto alone consults app-local choice. Off suppresses failures AND escalation.
        cases = [('auto', None, 'auto'), ('auto', 'off', 'off'), ('auto', 'owner', 'owner'),
                 ('auto', 'agent', 'agent'), ('owner', 'off', 'owner'), ('agent', 'off', 'agent'),
                 ('off', 'owner', 'off')]
        for env, local, mode in cases:
            for responder in (False, True):
                with self.subTest(env=env, local=local, responder=responder), tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as tmp:
                    root = Path(tmp); cron = root / 'cron'; output = cron / 'output/job1'; output.mkdir(parents=True)
                    jobs = [{'id': 'job1', 'name': 'Backup check'}]
                    if responder: jobs.append({'id': 'respond', 'name': 'Failure responder'})
                    (cron / 'jobs.json').write_text(json.dumps({'jobs': jobs}))
                    ns = load(root, {'CRON_FAILURES': env, 'CRON_QUIET_JOBS': 'job1'}, local)
                    self.assertEqual(ns['CRON_FAILURE_MODE'], mode)
                    now = time.time(); old = now - 3600
                    p = output / 'failure.md'
                    p.write_text('# Cron Job: Backup check\n\n**Status:** failed\n\n## Error\n\nDisk full\n')
                    os.utime(p, (old, old))
                    posts = []
                    def post(*args): posts.append(args); return True
                    ns['cron_relay_once'](str(cron), str(root / 'relay.json'), post, now)
                    queued = mode == 'agent' or mode == 'auto' and responder
                    self.assertEqual(len(posts), 0 if mode == 'off' or queued else 1)
                    if mode == 'off':
                        self.assertEqual(json.loads((root / 'relay.json').read_text())['failure_alerts'], {})
                        self.assertFalse((root / 'cron-failures.json').exists())
                    ns['cron_relay_once'](str(cron), str(root / 'relay.json'), post, now + 25 * 3600)
                    self.assertEqual(len(posts), 0 if mode == 'off' else 1)
                    if queued:
                        self.assertTrue(posts[0][0].startswith('Still failing:'))
                        self.assertTrue(json.loads((root / 'cron-failures.json').read_text())['job1']['escalated'])
                    before = len(posts)
                    p = output / 'recovered.md'
                    p.write_text('# Cron Job: Backup check\n\n**Status:** ok\n\n## Response\n\nAll three backup copies finished successfully and the restored disk has room again.\n')
                    os.utime(p, (now + 25 * 3600 + 1, now + 25 * 3600 + 1))
                    ns['cron_relay_once'](str(cron), str(root / 'relay.json'), post, now + 25 * 3600 + 30)
                    state = json.loads((root / 'relay.json').read_text())
                    self.assertEqual(state['failure_alerts'], {})
                    if queued: self.assertEqual(json.loads((root / 'cron-failures.json').read_text()), {})
                    self.assertEqual(len(posts), before if mode == 'off' else before + 1)
                    if mode != 'off': self.assertEqual(posts[-1][0], 'Recovered: Backup check')

    def test_acknowledgement_prevents_escalation(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as tmp:
            ns = load(Path(tmp), {'CRON_FAILURES': 'agent'})
            queue = {'job1': {'first_seen': 1, 'name': 'Backup', 'body': 'disk full'}}
            posts = []
            ns['_escalate'](queue, {'job1': 1}, lambda *a: posts.append(a) or True, 25 * 3600)
            self.assertEqual(posts, [])


if __name__ == '__main__':
    unittest.main()
