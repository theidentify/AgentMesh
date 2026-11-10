"""One-click join (J1 of docs/superpowers/specs/2026-11-10-one-click-join-design.md).

A v2 invite link carries everything a joiner needs before it has storage access, and **no storage credential**:

1. The joiner's device builds a **join request**: every object a first ``broker sync`` would upload (account,
   device certificate, profile recipient, the signed invite use), plus a fresh age recipient for the answer. The
   request is signed by the joining device and travels through a **channel**: a presigned PUT into the workspace
   store (``storage``), a shared folder (``folder``) or a file sent by hand (``file``).
2. An owner or admin checks it exactly as ``invite accept`` checks a use, approves it with presence, and writes a
   **join answer**: the verdict, and for an approval the member's storage credential sealed to the request's
   recipient. Only that request's device can open it.
3. The joiner stores the credential in its OS store, writes ``remote.json`` and syncs like any other device.

Channels and issuers only carry things. Who may join is decided by the signed invite, the admin's presence and the
signatures every reader checks, so a channel never widens anything.
"""
import base64
import json
import os
import re
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit
from .encoding import fields, hex_bytes, milliseconds, parse, wire
from .identity import sign, verify_signature

REQUEST = 'agentmesh-join-request-v1'
ANSWER = 'agentmesh-join-answer-v1'
REQUEST_DOMAIN = b'AgentMesh/join-request/Ed25519/v1\x00'
REQUEST_FIELDS = {'format', 'workspace', 'invite', 'slot', 'account', 'device_id', 'answer_recipient', 'objects',
                  'issued_at', 'signature'}
ANSWER_FIELDS = {'format', 'workspace', 'slot', 'verdict', 'reason', 'sealed', 'issued_at'}
MAX_REQUEST = 256 * 1024
PROVIDERS = ('r2', 'aws', 'minio', 's3')
CHANNELS = ('storage', 'folder', 'file')
STATES = ('requested', 'waiting_for_admin', 'waiting_for_storage', 'syncing', 'ready', 'rejected', 'expired', 'failed')
REQ_PREFIX, ANS_PREFIX = 'join/req/', 'join/ans/'


def b64(data): return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def unb64(text, limit=MAX_REQUEST):
    if type(text) is not str or len(text) > limit * 2 or not re.fullmatch(r'[A-Za-z0-9_-]*', text): raise ValueError('invalid encoding')
    raw = base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))
    if b64(raw) != text: raise ValueError('invalid encoding')
    return raw


def request_key(slot): hex_bytes(slot, 16); return REQ_PREFIX + slot + '.json'


def answer_key(slot): hex_bytes(slot, 16); return ANS_PREFIX + slot + '.json'


# ---- shapes carried in the link -------------------------------------------------------------------------------

def _https(url):
    if type(url) is not str or len(url) > 4096 or any(ord(c) <= 32 or ord(c) == 127 for c in url): raise ValueError('invalid url')
    parts = urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.fragment: raise ValueError('invalid url')
    return url


def shape_remote(remote):
    from .storage import validate_key
    from .storage.r2creds import S3_KINDS
    if type(remote) is not dict: raise ValueError('invalid remote')
    if remote.get('kind') == 'filesystem':
        if set(remote) != {'kind'}: raise ValueError('invalid remote')
        return remote
    if set(remote) != {'kind', 'provider', 'endpoint', 'region', 'bucket', 'prefix', 'path_style', 'credentials_kind'} or remote['kind'] != 's3':
        raise ValueError('invalid remote')
    _https(remote['endpoint'])
    if urlsplit(remote['endpoint']).path not in ('', '/') or urlsplit(remote['endpoint']).query: raise ValueError('invalid remote')
    if remote['provider'] not in PROVIDERS or remote['credentials_kind'] not in S3_KINDS or type(remote['path_style']) is not bool:
        raise ValueError('invalid remote')
    if type(remote['bucket']) is not str or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', remote['bucket']): raise ValueError('invalid remote')
    if type(remote['region']) is not str or not re.fullmatch(r'[a-z0-9-]{1,32}', remote['region']): raise ValueError('invalid remote')
    if type(remote['prefix']) is not str: raise ValueError('invalid remote')
    if remote['prefix']: validate_key(remote['prefix'], prefix=True)
    return remote


def shape_channels(channels):
    if type(channels) is not list or not 1 <= len(channels) <= len(CHANNELS): raise ValueError('invalid channels')
    seen = set()
    for c in channels:
        if type(c) is not dict or c.get('kind') not in CHANNELS or c['kind'] in seen: raise ValueError('invalid channels')
        seen.add(c['kind'])
        if c['kind'] == 'storage':
            if set(c) != {'kind', 'request_url', 'answer_url'}: raise ValueError('invalid channels')
            _https(c['request_url']); _https(c['answer_url'])
        elif set(c) != {'kind'}: raise ValueError('invalid channels')
    return channels


def remote_of(storage):
    """The link's ``remote`` for an admin's storage object (no credentials, ever)."""
    from .storage.filesystem import FilesystemStorage
    if isinstance(storage, FilesystemStorage): return {'kind': 'filesystem'}
    meta = storage.client.meta
    endpoint = meta.endpoint_url
    provider = 'r2' if '.r2.cloudflarestorage.com' in endpoint else 'aws' if endpoint.endswith('.amazonaws.com') else 's3'
    style = getattr(getattr(meta.config, 's3', None) or {}, 'get', lambda *_: None)('addressing_style') == 'path'
    return dict(kind='s3', provider=provider, endpoint=endpoint, region=meta.region_name or 'auto', bucket=storage.bucket,
                prefix=storage.prefix.rstrip('/'), path_style=style, credentials_kind='s3' if provider == 'r2' else 's3-generic')


def presign(storage, key, method, seconds):
    """A SigV4 presigned URL for one object; at most 7 days, the SigV4 limit and the invite limit."""
    if not 60 <= seconds <= 604800: raise ValueError('invalid_request')
    operation = {'PUT': 'put_object', 'GET': 'get_object'}[method]
    return _https(storage.client.generate_presigned_url(operation, Params={'Bucket': storage.bucket, 'Key': storage.prefix + key},
                                                        ExpiresIn=seconds))


# ---- request -------------------------------------------------------------------------------------------------

def allowed_object(key, workspace):
    """Objects a joiner may hand over: its own registry and key records. Never workspace entries, other admin
    records, events, or anything of the workspace profile except its own profile recipient."""
    if not key.startswith(('registry/', 'keyring/', 'keys/')): return False
    if key.startswith(('registry/workspace/', 'registry/chat-links/', 'registry/attestations/', 'registry/storage-credentials/',
                       'registry/device-classes/', f'keyring/{workspace}/', f'keys/{workspace}/')): return False
    return True


def build_request(state, local, token, recipient):
    objects = {key: b64(local.get(key)) for key in local.list('') if allowed_object(key, token['workspace'])}
    record = dict(format=REQUEST, workspace=token['workspace'], invite=token['invite'], slot=token['slot'],
                  account=state.account.account_id, device_id=state.device.device_id, answer_recipient=recipient,
                  objects=objects, issued_at=int(time.time() * 1000))
    data = wire(sign(record, state.device._private, REQUEST_DOMAIN))
    if len(data) > MAX_REQUEST: raise ValueError('request too large')
    return data


def read_request(data):
    if type(data) is not bytes or len(data) > MAX_REQUEST: raise ValueError('invalid request')
    record = parse(data)
    fields(record, REQUEST_FIELDS, REQUEST)
    for name in ('workspace', 'invite', 'slot', 'account', 'device_id'): hex_bytes(record[name], 16)
    milliseconds(record['issued_at']); hex_bytes(record['signature'], 64)
    from .agekeys import validate_recipient
    validate_recipient(record['answer_recipient'])
    if type(record['objects']) is not dict: raise ValueError('invalid request')
    for key, value in record['objects'].items():
        from .storage import validate_key
        validate_key(key); unb64(value)
        if not allowed_object(key, record['workspace']): raise ValueError('invalid request')
    return record


class Overlay:
    """The admin's replica with a request's objects added on top, read-only: lets the existing checks run before
    anything is written."""
    def __init__(self, local, objects): self.local, self.extra = local, {k: unb64(v) for k, v in objects.items()}
    def get(self, key):
        from .storage import ObjectNotFound
        try: return self.local.get(key)
        except ObjectNotFound:
            if key in self.extra: return self.extra[key]
            raise
    def list(self, prefix, *, start_after=None):
        keys = set(self.local.list(prefix)) | {k for k in self.extra if k.startswith(prefix)}
        return iter(sorted(k for k in keys if start_after is None or k > start_after))
    def put(self, key, data): raise ValueError('read-only')


def check_request(record, local, invite):
    """(use, reason): the invite use inside the request, and why it cannot be accepted (None when it can)."""
    from . import invites
    from .registry_store import RegistryStore
    from .federation import account_record
    view = Overlay(local, record['objects'])
    uses = []
    for key, value in record['objects'].items():
        if not key.startswith(invites.PREFIX + record['workspace'] + '/'): continue
        try:
            use = invites.shape_use(parse(unb64(value)))
            if key == invites.use_path(use) and use['account'] == record['account'] and use['device_id'] == record['device_id'] \
                    and use['invite'] == record['invite']: uses.append(use)
        except (ValueError, KeyError, TypeError): continue
    if len(uses) != 1: return None, 'no invite use'
    use = uses[0]
    reason = invites.check_use(use, view, invite)
    if reason is not None: return use, reason
    try:
        registry = RegistryStore(view, account_record(use['account'], use['account_root'])).load()
        verify_signature(record, registry.device_public(record['device_id'], at=record['issued_at']), REQUEST_DOMAIN)
    except Exception: return use, 'bad request signature'
    return use, None


def apply_request(record, local):
    """Write the request's objects the admin replica does not have yet. Objects are immutable: an existing key is
    left as it is (shared paths such as ``registry/account.json`` belong to whoever wrote them first)."""
    from .storage import ObjectNotFound
    written = 0
    for key, value in sorted(record['objects'].items()):
        try: local.get(key)
        except ObjectNotFound: local.put(key, unb64(value)); written += 1
    return written


# ---- answer --------------------------------------------------------------------------------------------------

def build_answer(workspace, slot, verdict, reason, recipient, credentials):
    from .agekeys import encrypt
    if verdict not in ('approved', 'rejected'): raise ValueError('invalid verdict')
    sealed = b64(encrypt(wire({'credentials': credentials}), recipient)) if verdict == 'approved' else None
    return wire(dict(format=ANSWER, workspace=workspace, slot=slot, verdict=verdict, reason=reason, sealed=sealed,
                     issued_at=int(time.time() * 1000)))


def open_answer(data, workspace, slot, identity):
    """(verdict, reason, credentials). The answer is not signed: anyone able to write the slot could only refuse
    or hand over credentials that fail, and everything after it is checked against the pinned owner."""
    record = parse(data)
    fields(record, ANSWER_FIELDS, ANSWER)
    if record['workspace'] != workspace or record['slot'] != slot or record['verdict'] not in ('approved', 'rejected'):
        raise ValueError('invalid answer')
    reason = record['reason'] if type(record['reason']) is str and len(record['reason']) <= 200 else None
    if record['verdict'] == 'rejected': return 'rejected', reason, None
    inner = parse(identity.decrypt(unb64(record['sealed'])))
    if type(inner) is not dict or set(inner) != {'credentials'}: raise ValueError('invalid answer')
    creds = inner['credentials']
    if creds is not None:
        if type(creds) is not dict or set(creds) != {'kind', 'access_key_id', 'secret_access_key'}: raise ValueError('invalid answer')
        from .storage.r2creds import check_values, S3_KINDS
        if creds['kind'] not in S3_KINDS: raise ValueError('invalid answer')
        check_values(creds['kind'], (creds['access_key_id'], creds['secret_access_key']))
    return 'approved', reason, creds


# ---- channels (joiner side) -----------------------------------------------------------------------------------

def http(method, url, data=None, *, opener=None):
    """One request to a presigned URL: (status, body). No credentials are sent; the URL carries the signature."""
    import urllib.request, urllib.error
    if opener is not None: return opener(method, url, data)
    request = urllib.request.Request(_https(url), data=data, method=method)
    if data is not None: request.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(request, timeout=20) as response: return response.status, response.read(MAX_REQUEST + 1)
    except urllib.error.HTTPError as exc: return exc.code, b''


def send_request(token, data, *, folder=None, request_out=None, opener=None):
    """Send through the first channel that is usable here; returns its kind."""
    from .storage.filesystem import FilesystemStorage
    for channel in token['channels']:
        if channel['kind'] == 'storage':
            status, _ = http('PUT', channel['request_url'], data, opener=opener)
            if 200 <= status < 300: return 'storage'
            raise ValueError('request not delivered')
        if channel['kind'] == 'folder' and folder is not None:
            FilesystemStorage(folder).put(request_key(token['slot']), data); return 'folder'
        if channel['kind'] == 'file' and request_out is not None:
            out = Path(request_out); fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as stream: stream.write(data)
            return 'file'
    raise ValueError('no usable channel (pass --folder or --request-out)')


def fetch_answer(token, channel, *, folder=None, answer_file=None, opener=None):
    """The answer bytes, or None while there is none yet."""
    from .storage import ObjectNotFound
    from .storage.filesystem import FilesystemStorage
    if channel == 'storage':
        url = next(c['answer_url'] for c in token['channels'] if c['kind'] == 'storage')
        status, body = http('GET', url, opener=opener)
        if status == 200: return body
        if status in (403, 404): return None   # 403: S3 hides a missing key from signers without ListBucket
        raise ValueError('answer not readable')
    if channel == 'folder':
        try: return FilesystemStorage(folder).get(answer_key(token['slot']))
        except ObjectNotFound: return None
    if answer_file is not None and Path(answer_file).exists(): return Path(answer_file).read_bytes()
    return None


# ---- joiner state ------------------------------------------------------------------------------------------------

def joins_path(home): return Path(home.root) / 'joins.json'


def read_joins(home):
    try: value = json.loads(joins_path(home).read_text(encoding='utf-8'))
    except FileNotFoundError: return []
    return value if type(value) is list else []


def write_join(home, entry):
    """Content-free state per workspace (no titles, no memory): what the client shows without unlocking."""
    from .broker.keystore import atomic_write
    if entry['state'] not in STATES: raise ValueError('invalid state')
    entries = [e for e in read_joins(home) if e.get('workspace') != entry['workspace']] + [dict(entry, updated_at=int(time.time() * 1000))]
    atomic_write(joins_path(home), json.dumps(entries, sort_keys=True).encode('utf-8'))
    return entry


def new_slot(): return secrets.token_hex(16)
