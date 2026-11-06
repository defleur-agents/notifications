"""Focused synthetic gate. Run ONLY in the network-none worker container."""
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
import threading
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
MAIL = Path(os.environ['MAIL_SOURCE'])
sys.path.insert(0, str(MAIL / 'app'))
from monitor import NotificationSink, Monitor
from physical import PhysicalMailbox

TOKEN = 'synthetic-mail-grant-no-owner-secrets'
EVENT = 'physical:' + 'a' * 64


def source():
    text = (ROOT / 'mcp/front.py').read_text() + '\n\n# ---- tools for this app ----\n' + (ROOT / 'mcp/tools.py').read_text()
    for name in ('minimal_assets.py', 'relay.py', 'readable.py', 'mail_socket.py'):
        text += '\n' + (ROOT / 'mcp' / name).read_text()
    return text


def load(root):
    module = types.ModuleType('fixture_front')
    exec(compile(source(), 'candidate-front', 'exec'), module.__dict__)
    module.GRANTS_FILE = str(root / 'incoming.json')
    module.READABLE_DB = str(root / 'readable.sqlite')
    module.INBOX_STATE = str(root / 'inbox.json')
    module.INBOX_TOPICS = ['wizard', 'cron']
    # No models, provider or real ntfy: accidental calls fail rather than escape.
    module.router_rewrite = lambda payload: (_ for _ in ()).throw(AssertionError('unconfigured synthetic editor'))
    module.http = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('no live publish allowed'))
    return module


class Fixture:
    def __init__(self, root, module):
        self.root, self.module = root, module
        self.path = str(root / 'mail.sock')
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.server = asyncio.run_coroutine_threadsafe(module.start_mail_socket(self.path), self.loop).result(5)

    def close(self):
        async def stop():
            self.server.close()
            await self.server.wait_closed()
        asyncio.run_coroutine_threadsafe(stop(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        self.loop.close()

    def request(self, request):
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(5)
            s.connect(self.path)
            s.sendall(json.dumps(request).encode() + b'\n')
            return json.loads(s.makefile('rb').readline(2048))


class SocketTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='m-', dir=os.environ['TMPDIR'])
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.root.chmod(0o700)
        self.grants(True)
        self.module = load(self.root)
        self.mode = 'fyi'
        self.editor_calls = []
        self.published = []
        self.configure(self.module)
        self.fixture = Fixture(self.root, self.module)
        self.addCleanup(lambda: self.fixture.close())
        outgoing = self.root / 'outgoing.json'
        outgoing.write_text(json.dumps({'outgoing': {'notifications': TOKEN}}))
        self.sink = NotificationSink(str(outgoing)); self.sink.PATH = self.fixture.path

    def configure(self, module):
        def rewrite(payload):
            self.editor_calls.append(payload)
            if self.mode == 'error':
                raise RuntimeError('synthetic editor unavailable')
            body = 'New physical mail is ready. What to do: Open Mail to review.' if self.mode == 'needs_you' else 'Your physical mail changed. Nothing to do.'
            return {'verdict': self.mode, 'title': 'Mail update', 'body': body}, {'fixture': True}
        def http(method, path, body, headers, **kwargs):
            self.assertEqual((method, path), ('POST', '/'))
            self.published.append(json.loads(body))
            return 200, json.dumps({'id': 'synthetic-push', 'event': 'message', 'time': 1}).encode()
        module.router_rewrite, module.http = rewrite, http

    def grants(self, enabled):
        (self.root / 'incoming.json').write_text(json.dumps({'enforce': False, 'incoming':
            [{'from': 'wizard-mail', 'sha256': hashlib.sha256(TOKEN.encode()).hexdigest()}] if enabled else []}))

    def req(self, **changes):
        return dict({'method': 'submit', 'grant': TOKEN, 'event_id': EVENT, 'new_items': 1, 'scan_changes': 2}, **changes)

    def test_auth_per_request_wrong_revoked_missing_and_peer(self):
        self.assertTrue(self.sink.ready())
        self.assertEqual(self.fixture.request({'method': 'ready', 'grant': 'wrong-synthetic-token'}), {'error': 'unauthorized'})
        self.assertEqual(self.fixture.request({'method': 'ready'}), {'error': 'unauthorized'})
        self.grants(False); self.assertFalse(self.sink.ready())
        self.grants(True); self.assertTrue(self.sink.ready())
        self.module._MAIL_UID = os.getuid() + 1
        self.assertFalse(self.sink.ready())
        self.assertEqual(self.editor_calls, []); self.assertEqual(self.published, [])

    def test_methods_keys_body_counts_and_URL_refused(self):
        for patch in ({'method': 'tools/list'}, {'method': 'notifications_recent'}, {'message': 'no'},
                      {'url': 'https://example.test/'}, {'tool': 'notify'}, {'provider': 'x'}, {'history': True},
                      {'new_items': -1}, {'new_items': True}, {'scan_changes': 100001}, {'event_id': 'other'},
                      {'new_items': 0, 'scan_changes': 0}):
            with self.subTest(patch=patch):
                self.assertEqual(self.fixture.request(self.req(**patch)), {'error': 'invalid_request'})
        self.assertEqual(self.fixture.request(['not', 'object']), {'error': 'unauthorized'})
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(5); s.connect(self.fixture.path); s.sendall(b'x' * 1200 + b'\n')
            self.assertEqual(s.recv(10), b'')
        self.assertEqual(self.editor_calls, []); self.assertEqual(self.published, [])

    def test_fixed_payload_and_durable_idempotency_across_front_restart(self):
        receipt = self.sink.send(EVENT, 1, 2)
        self.assertEqual(receipt, {'event_id': EVENT, 'accepted': True, 'status': 'accepted', 'pushed': False})
        self.assertEqual(self.sink.send(EVENT, 1, 2), receipt)
        self.assertEqual(self.sink.confirm(EVENT), receipt)
        self.assertEqual(len(self.editor_calls), 1)
        self.assertEqual(self.editor_calls[0], self.module.mail_payload({'new_items': 1, 'scan_changes': 2}))
        self.assertEqual(self.editor_calls[0]['click'], NotificationSink.LINK)
        self.assertNotIn('grant', self.editor_calls[0])
        self.fixture.close()
        self.module = load(self.root); self.configure(self.module)
        self.fixture = Fixture(self.root, self.module)
        self.assertEqual(self.sink.confirm(EVENT), receipt)
        self.assertEqual(self.sink.send(EVENT, 1, 2), receipt)
        self.assertEqual(len(self.editor_calls), 1)
        self.assertEqual(self.fixture.request(self.req(new_items=3)), {'error': 'event_conflict'})
        self.assertEqual(self.sink.confirm('physical:' + 'b' * 64)['status'], 'absent')
        self.assertEqual(self.published, [])

    def test_queued_suppressed_and_published_are_distinct(self):
        self.mode = 'error'; self.assertEqual(self.sink.send(EVENT, 1, 0)['status'], 'queued')
        self.assertEqual(len(self.editor_calls), 1)
        self.sink.send(EVENT, 1, 0); self.assertEqual(len(self.editor_calls), 1)
        with self.module.readable_db() as db:
            db.execute('UPDATE updates SET retry_at=0')
        self.grants(False); self.module.readable_retry_once()
        self.assertEqual(len(self.editor_calls), 1)
        self.grants(True); self.mode = 'noise'; self.module.readable_retry_once()
        self.assertEqual(self.sink.confirm(EVENT)['status'], 'suppressed')
        self.mode = 'needs_you'
        second = self.sink.send('physical:' + 'b' * 64, 0, 1)
        self.assertEqual((second['status'], second['pushed']), ('published', True))
        self.assertEqual(len(self.published), 1)  # synthetic sink only
        self.assertEqual(self.published[0]['click'], NotificationSink.LINK)

    def test_socket_lifecycle_permissions_collisions_and_missing_listener(self):
        self.assertEqual(os.stat(self.fixture.path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.root).st_mode & 0o777, 0o700)
        async def collide():
            with self.assertRaisesRegex(RuntimeError, 'already live'):
                await self.module.start_mail_socket(self.fixture.path)
        asyncio.run(collide())
        path = self.root / 'bad.sock'; path.symlink_to(self.root / 'unrelated')
        with self.assertRaises(RuntimeError): asyncio.run(self.module.start_mail_socket(str(path)))
        self.sink.PATH = str(self.root / 'missing.sock'); self.assertFalse(self.sink.ready())
        parent = self.root / 'link'; parent.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(RuntimeError): asyncio.run(self.module.start_mail_socket(str(parent / 'x')))

    def test_adapter_and_real_monitor_consent_baseline_delta_authstop(self):
        spec = importlib.util.spec_from_file_location('mail_monitor_fixture', MAIL / 'tests/test_monitor.py')
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        provider = m.Provider(); physical = self.root / 'physical'; physical.mkdir(mode=0o700)
        (physical / 'provider.env').write_text("EARTHCLASSMAIL_API_KEY='synthetic-only'\n")
        box = PhysicalMailbox(physical, client=provider)
        now = [1800000000]
        monitor = Monitor(box, physical, self.sink, clock=lambda: now[0])
        monitor.tick(); self.assertEqual(provider.calls, [])
        box.inboxes(); monitor.tick(); self.assertEqual(len(provider.calls), 1)
        with monitor.connect() as db:
            monitor.put(db, 'enabled', True)
            monitor.put(db, 'credential', list(box.credential_stamp()))
        monitor.tick(); self.assertEqual(len(provider.calls), 1)
        self.assertFalse(monitor.status()['enabled'])  # Old enabled without socket consent is inert.
        self.grants(False)
        with self.assertRaises(m.MailError): monitor.enable()
        self.assertFalse(monitor.status()['enabled'])
        self.grants(True); monitor.enable(); monitor.tick()
        self.assertEqual(monitor.alerts(), [])
        provider.items.append({'mail_item_id': 'new', 'digital_status': 'PENDING'})
        now[0] += 301; monitor.tick()
        self.assertEqual(monitor.alerts()[0]['delivery'], 'accepted')
        self.assertEqual(len(self.editor_calls), 1)
        self.grants(False); before = len(provider.calls); now[0] += 301; monitor.tick()
        self.assertEqual(len(provider.calls), before)
        self.assertFalse(monitor.status()['enabled'])
        self.assertTrue((physical / 'provider.env').exists())
        self.assertTrue(box.status()['connected'])
        self.grants(True); monitor.enable()
        socket_path = self.sink.PATH; self.sink.PATH = str(self.root / 'absent.sock')
        now[0] += 301; before = len(provider.calls); monitor.tick()
        self.assertTrue(monitor.status()['enabled'])
        self.assertEqual(monitor.status()['state'], 'retrying')
        self.assertEqual(len(provider.calls), before)
        self.sink.PATH = socket_path; monitor.tick()
        self.assertEqual(monitor.status()['state'], 'watching')

    def test_optional_listener_failure_preserves_existing_TCP_front(self):
        async def exercise():
            # Hold a concrete free loopback port, then let the actual main bind it.
            probe = socket.socket(); probe.bind(('127.0.0.1', 0))
            self.module.PORT = probe.getsockname()[1]; probe.close()
            async def bad_directory():
                self.root.chmod(0o755)
                try:
                    self.module.mail_socket_directory(self.fixture.path)
                finally:
                    self.root.chmod(0o700)
            self.module.start_mail_socket = bad_directory
            previous = os.environ.get('MAIL_SOCKET_ENABLED')
            os.environ['MAIL_SOCKET_ENABLED'] = 'on'
            task = asyncio.create_task(self.module.main())
            try:
                await asyncio.sleep(0.05)
                self.assertFalse(task.done(), 'optional route failure killed the main front')
                reader, writer = await asyncio.open_connection('127.0.0.1', self.module.PORT)
                body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}).encode()
                writer.write(b'POST /mcp HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n\r\n' + body)
                await writer.drain()
                head = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), timeout=3)
                self.assertIn(b'200', head.split(b'\r\n')[0])
                writer.close(); await writer.wait_closed()
            finally:
                task.cancel()
                try: await task
                except asyncio.CancelledError: pass
                if previous is None: os.environ.pop('MAIL_SOCKET_ENABLED', None)
                else: os.environ['MAIL_SOCKET_ENABLED'] = previous
        asyncio.run(exercise())

    def test_legacy_outbox_migration_retains_rows_and_unknown_payload(self):
        physical = self.root / 'old'; physical.mkdir(mode=0o700)
        db = sqlite3.connect(physical / 'monitor.sqlite3')
        db.executescript('CREATE TABLE alerts(key TEXT PRIMARY KEY,message TEXT NOT NULL,created INTEGER,delivery TEXT NOT NULL DEFAULT "pending");')
        db.executemany('INSERT INTO alerts(key,message,created) VALUES (?,?,?)', [
            (EVENT, '2 new physical mail item(s); 3 scan status change(s), checked yesterday.', 1),
            ('unknown', 'unrecognized legacy message', 2)])
        db.execute("UPDATE alerts SET delivery='unknown'")
        db.commit(); db.close()
        monitor = Monitor(PhysicalMailbox(physical), physical, self.sink)
        monitor.deliver()
        self.assertEqual(self.editor_calls, [])
        db = sqlite3.connect(physical / 'monitor.sqlite3')
        self.assertEqual(db.execute('SELECT count(*) FROM alerts').fetchone()[0], 2)
        self.assertEqual(db.execute('SELECT new_items,scan_changes FROM alerts WHERE key=?', (EVENT,)).fetchone(), (2, 3))
        self.assertEqual(db.execute('SELECT new_items FROM alerts WHERE key="unknown"').fetchone(), (None,))
        db.close()


if __name__ == '__main__':
    unittest.main()
