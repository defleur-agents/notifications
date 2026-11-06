# Optional Mail-only count ingress; deliberately NOT an MCP or HTTP proxy.
import hmac
import stat
import socket
import struct
import hashlib
import re
import json
import os
import asyncio
from pathlib import Path

MAIL_SOCKET = '/run/mail-notifications/mail.sock'
MAIL_LINK = 'https://wizard.tailc0f369.ts.net/wizard-os-home/?session=wizard_1793977271974_217d03a6c3be'
_MAIL_EVENT = re.compile(r'physical:[a-f0-9]{64}\Z')
_MAIL_UID = 1000
_MAIL_LIMIT = 1024
_mail_slots = None


def mail_grant_hashes():
    # Never inherit the optional general MCP enforcement flag. Missing/revoked is closed.
    try:
        doc = json.loads(Path(GRANTS_FILE).read_text())
        return [v['sha256'] for v in doc.get('incoming', [])
                if isinstance(v, dict) and v.get('from') == 'wizard-mail'
                and isinstance(v.get('sha256'), str) and re.fullmatch('[a-f0-9]{64}', v['sha256'])]
    except (OSError, ValueError, TypeError, AttributeError):
        return []


def mail_authorized(grant):
    if not isinstance(grant, str) or not 16 <= len(grant) <= 256:
        return False
    digest = hashlib.sha256(grant.encode()).hexdigest()
    return any(hmac.compare_digest(digest, expected) for expected in mail_grant_hashes())


def mail_payload(event):
    return {'topic': TOPIC, 'title': 'Physical mail', 'priority': 3, 'tags': ['mail'],
            'click': MAIL_LINK,
            'message': '%d new physical mail item(s); %d scan status change(s). Open Mail to review. No action has been taken on your mail.' %
                       (event['new_items'], event['scan_changes'])}


def mail_receipt(row, event_id):
    if row is None:
        return {'event_id': event_id, 'accepted': False, 'status': 'absent', 'pushed': False}
    sent = json.loads(row['sent'] or '{}')
    result = json.loads(row['result'] or '{}')
    pushed = sent.get('pushed') is True
    status = ('published' if pushed else 'queued' if row['pending'] else
              'suppressed' if result.get('verdict') == 'noise' else 'accepted')
    return {'event_id': event_id, 'accepted': True, 'status': status, 'pushed': pushed}


def mail_request(request):
    if not isinstance(request, dict) or not mail_authorized(request.get('grant')):
        return {'error': 'unauthorized'}
    method = request.get('method')
    keys = set(request)
    if method == 'ready' and keys == {'method', 'grant'}:
        # No models/provider/history/publish calls. Prove durable receipt storage is available.
        with readable_db() as db:
            db.execute('SELECT 1 FROM updates LIMIT 0')
        return {'ready': True, 'protocol': 1}
    if method not in ('submit', 'receipt'):
        return {'error': 'invalid_request'}
    expected = {'method', 'grant', 'event_id'}
    if method == 'submit':
        expected |= {'new_items', 'scan_changes'}
    event_id = request.get('event_id')
    if keys != expected or not isinstance(event_id, str) or not _MAIL_EVENT.fullmatch(event_id):
        return {'error': 'invalid_request'}
    if method == 'submit' and (any(type(request[k]) is not int or not 0 <= request[k] <= 100000
                                    for k in ('new_items', 'scan_changes'))
                               or not request['new_items'] + request['scan_changes']):
        return {'error': 'invalid_request'}
    uid = 'mail-' + event_id.split(':')[1]
    with _READABLE_LOCK:
        if not mail_authorized(request['grant']):
            return {'error': 'unauthorized'}
        with readable_db() as db:
            row = db.execute('SELECT * FROM updates WHERE id=?', (uid,)).fetchone()
        if method == 'receipt':
            return mail_receipt(row, event_id)
        payload = mail_payload(request)
        if row is not None:
            if json.loads(row['raw']) != payload:
                return {'error': 'event_conflict'}
            return mail_receipt(row, event_id)
        # Recheck after waiting for the editor lock: revocation while queued is closed.
        if not mail_authorized(request['grant']):
            return {'error': 'unauthorized'}
        try:
            rewrite_and_deliver(payload, uid=uid)
        except Exception:
            # Existing Notifications retry policy owns any durable admission, not Mail retries.
            pass
        with readable_db() as db:
            row = db.execute('SELECT * FROM updates WHERE id=?', (uid,)).fetchone()
        return mail_receipt(row, event_id)


def mail_socket_directory(path):
    if not os.path.isabs(path) or len(os.fsencode(path)) > 107:
        raise RuntimeError('unsafe Mail socket path')
    directory = os.path.dirname(path)
    # All ancestors must be real directories, never traversal through symlinks.
    current = Path(directory)
    while True:
        st = current.lstat()
        if not stat.S_ISDIR(st.st_mode):
            raise RuntimeError('unsafe Mail socket directory')
        if current == Path(directory) and (st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700):
            raise RuntimeError('unsafe Mail socket directory owner/mode')
        if current.parent == current:
            break
        current = current.parent
    return directory


async def mail_handle(reader, writer):
    try:
        peer = writer.get_extra_info('socket').getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        if struct.unpack('3i', peer)[1] != _MAIL_UID:
            result = {'error': 'unauthorized'}
        else:
            async with _mail_slots:
                raw = await asyncio.wait_for(reader.readline(), timeout=3)
                if not raw.endswith(b'\n') or len(raw) > _MAIL_LIMIT:
                    result = {'error': 'invalid_request'}
                else:
                    result = await asyncio.to_thread(mail_request, json.loads(raw))
        writer.write(json.dumps(result).encode() + b'\n')
        await writer.drain()
    except Exception:
        # Never echo input, credentials, mail content, models or internal exceptions.
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


async def start_mail_socket(path=MAIL_SOCKET):
    global _mail_slots
    mail_socket_directory(path)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        st = None
    if st is not None:
        if not stat.S_ISSOCK(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o600:
            raise RuntimeError('unsafe existing Mail socket')
        probe = socket.socket(socket.AF_UNIX)
        try:
            probe.settimeout(1)
            probe.connect(path)
        except ConnectionRefusedError:
            if os.lstat(path).st_ino != st.st_ino:
                raise RuntimeError('Mail socket changed')
            os.unlink(path)
        else:
            raise RuntimeError('Mail socket already live')
        finally:
            probe.close()
    _mail_slots = asyncio.Semaphore(8)
    mask = os.umask(0o177)
    try:
        # Bind synchronously under the restrictive umask, no chmod/path race after bind.
        sock = socket.socket(socket.AF_UNIX)
        sock.bind(path)
        sock.listen(16)
        sock.setblocking(False)
    finally:
        os.umask(mask)
    return await asyncio.start_unix_server(mail_handle, sock=sock, limit=_MAIL_LIMIT + 1)
