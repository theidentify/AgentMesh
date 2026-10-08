"""Versioned semantic mirrors over pre-shared HMAC channels, not asymmetric signatures.

Inline quotes attest only to an issuer's assertion; original source files are never
transferred or fetched. Mirrors retain portable owner identities. Keys must have
at least 32 bytes; all holders can forge envelopes. No key-management guarantee.

A bundle carries one owner's records; cross-owner derivations fail closed. Both
current and historical export/read/evidence policies must permit the recipient.
Revoked snapshots retain their immutable content and are sent only to policy-
authorized recipients, never newly unauthorized readers. Missing dependencies
quarantine the entire signed packet. Authenticated pending records conservatively
block foreign API reads, evidence, retention and search for their object IDs until
the packet commits. Owner/admin low-level inspection remains available. A pending
packet is not an applied ACK, and blocked objects cannot be re-exported.

Limits: 128 records and 1 MiB per envelope; pending storage is capped at 128
packets and 1 MiB total. 'conflict' is durable receipt, not a ready/applied ACK.
Replay returns 'duplicate' plus the original receipt status. No vectors, remote
source fetching, filename payloads, asymmetric signatures or automatic conflict
resolution are supported.
"""
import hashlib
import hmac
import json
import re
from .core import (
    canonical, digest, KnowledgeError, AccessDenied, PROFILE, KINDS,
    SOURCE_KINDS, policy_for, bounded_text, MemoryAPI, ProfileStore,
)

SHA = re.compile(r'sha256:[0-9a-f]{64}\Z')
IDENT = re.compile(r'urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z')
FIELDS = {'id', 'revision', 'owner', 'content', 'kind', 'project', 'status', 'parents', 'verification', 'evidence', 'derived_from', 'policy'}


def _token(value, pattern):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise KnowledgeError('invalid identifier or digest')


def _list(value):
    if not isinstance(value, list) or len(value) > 128:
        raise KnowledgeError('invalid bounded list')


def _record(record, issuer, recipient):
    if not isinstance(record, dict) or set(record) != FIELDS:
        raise KnowledgeError('invalid record schema')
    _token(record['id'], IDENT)
    _token(record['revision'], SHA)
    if record['owner'] != issuer:
        raise AccessDenied('record owner is not the authenticated issuer')
    bounded_text(record['content'], 'content')
    bounded_text(record['project'], 'project', 128)
    if not isinstance(record['kind'], str) or record['kind'] not in KINDS or record['status'] not in ('active', 'revoked') or record['verification'] != 'unverified':
        raise KnowledgeError('invalid semantic metadata')
    _list(record['parents'])
    for parent in record['parents']:
        _token(parent, SHA)
    if len(set(record['parents'])) != len(record['parents']) or record['revision'] in record['parents']:
        raise KnowledgeError('invalid parents')
    if record['policy'] != policy_for(issuer, record['policy']):
        raise KnowledgeError('policy must be normalized')
    _allowed(record, recipient)
    _list(record['evidence'])
    for evidence in record['evidence']:
        if not isinstance(evidence, dict) or set(evidence) != {'source_id', 'kind', 'locator', 'quote', 'availability'}:
            raise KnowledgeError('invalid evidence schema')
        _token(evidence['source_id'], SHA)
        if not isinstance(evidence['kind'], str) or evidence['kind'] not in SOURCE_KINDS or evidence['availability'] != 'inline':
            raise KnowledgeError('invalid evidence metadata')
        bounded_text(evidence['locator'], 'locator', 512)
        bounded_text(evidence['quote'], 'quote', 4096)
    _list(record['derived_from'])
    for ref in record['derived_from']:
        if not isinstance(ref, dict) or set(ref) != {'owner', 'id', 'revision'}:
            raise KnowledgeError('invalid derived reference')
        _profile(ref['owner'])
        _token(ref['id'], IDENT)
        _token(ref['revision'], SHA)
    if record['revision'] != digest({k: v for k, v in record.items() if k != 'revision'}):
        raise KnowledgeError('revision digest mismatch')


def _profile(value):
    if not isinstance(value, str) or not PROFILE.fullmatch(value):
        raise KnowledgeError('invalid profile ID')
    return value


def _key(key):
    if not isinstance(key, bytes) or len(key) < 32:
        raise KnowledgeError('channel key must contain at least 32 bytes')


MAX_BYTES = 1024 * 1024


def _bounded(envelope):
    try:
        data = canonical(envelope).encode('utf-8')
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise KnowledgeError('envelope is not canonical JSON') from exc
    if len(data) > MAX_BYTES:
        raise KnowledgeError('serialized bundle exceeds 1 MiB')
    _list(envelope['records'])
    return data


def _authenticate(store, bundle, recipient, issuer, key):
    _key(key)
    _profile(recipient)
    _profile(issuer)
    if not isinstance(bundle, dict) or set(bundle) != {'format', 'issuer', 'recipient', 'records', 'bundle_id', 'signature'}:
        raise KnowledgeError('invalid envelope schema')
    _bounded(bundle)
    if bundle['format'] != FORMAT or bundle['issuer'] != issuer or bundle['recipient'] != recipient or store.profile_id != issuer:
        raise AccessDenied('channel binding mismatch')
    unsigned = {k: v for k, v in bundle.items() if k != 'signature'}
    payload = {k: v for k, v in unsigned.items() if k != 'bundle_id'}
    _token(bundle['bundle_id'], SHA)
    _token(bundle['signature'], re.compile(r'[0-9a-f]{64}\Z'))
    if bundle['bundle_id'] != digest(payload):
        raise KnowledgeError('bundle identity mismatch')
    if not hmac.compare_digest(bundle['signature'], _sign(unsigned, key)):
        raise AccessDenied('channel authentication failed')

FORMAT = 'agentmesh-knowledge-bundle-v1'


def _sign(envelope, key):
    return hmac.new(key, canonical(envelope).encode('utf-8'), hashlib.sha256).hexdigest()


def _allowed(record, recipient):
    if any(recipient not in record['policy'][action] for action in ('read', 'evidence', 'export')):
        raise AccessDenied('recipient requires read, evidence and export permissions')


def export_bundle(api: MemoryAPI, recipient: str, ids: list[str], *, key: bytes, include_history: bool = True) -> dict:
    """Export owner-authorized current heads and dependency/ancestry closure."""
    _key(key)
    _profile(recipient)
    _list(ids)
    if not isinstance(include_history, bool):
        raise KnowledgeError('include_history must be a boolean')
    for ident in ids:
        _token(ident, IDENT)
    store = api.stores.get(api.principal)
    if store is None:
        raise AccessDenied('only the owning principal may export')
    records = {}
    with store.connection() as conn:
        conn.execute('BEGIN')

        def heads(ident):
            return [json.loads(row['data']) for row in conn.execute(
                'SELECT r.data FROM heads h JOIN revisions r USING(id,revision) WHERE h.id=? ORDER BY h.revision', (ident,))]

        def visit(ident, revision=None):
            if store.blocked(ident, conn):
                raise AccessDenied('pending knowledge cannot be re-exported')
            current = heads(ident)
            if not current:
                raise AccessDenied('knowledge or dependency is unavailable')
            for head in current:
                _allowed(head, recipient)
            if revision is None:
                for head in current:
                    visit(ident, head['revision'])
                return
            row = store.raw(ident, revision, conn)
            if not row or row['owner'] != api.principal:
                raise AccessDenied('only the owning principal may export')
            record = json.loads(row['data'])
            _record(record, api.principal, recipient)
            token = (ident, record['revision'])
            if token in records:
                return
            if len(records) >= 128:
                raise KnowledgeError('bundle record quota exceeded')
            records[token] = record
            for ref in record['derived_from']:
                if ref['owner'] != api.principal:
                    raise AccessDenied('cross-owner dependencies require a separately authenticated owner channel')
                dependency_heads = heads(ref['id'])
                if len(dependency_heads) != 1 or dependency_heads[0]['status'] != 'active':
                    raise AccessDenied('derived dependency is unavailable, conflicted or revoked')
                visit(ref['id'], ref['revision'])
                visit(ref['id'])
            if include_history:
                for parent in record['parents']:
                    visit(ident, parent)

        for ident in ids:
            visit(ident)
    envelope = dict(format=FORMAT, issuer=api.principal, recipient=recipient, records=sorted(records.values(), key=lambda r: (r['id'], r['revision'])))
    envelope['bundle_id'] = digest(envelope)
    envelope['signature'] = _sign(envelope, key)
    _bounded(envelope)
    return envelope


def _tables(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS exchange_receipts (bundle_id TEXT PRIMARY KEY, issuer TEXT NOT NULL, recipient TEXT NOT NULL, status TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS exchange_pending (bundle_id TEXT PRIMARY KEY, data TEXT NOT NULL)')
    conn.execute('''CREATE TABLE IF NOT EXISTS exchange_pending_blocks (
        id TEXT NOT NULL, bundle_id TEXT NOT NULL, PRIMARY KEY(id,bundle_id),
        FOREIGN KEY(bundle_id) REFERENCES exchange_pending(bundle_id) ON DELETE CASCADE)''')


def pending(store: ProfileStore) -> list[dict]:
    """Inspect persisted envelopes; inspection is not authentication or an ACK."""
    with store.connection() as conn:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='exchange_pending'").fetchone()
        if not exists:
            return []
        return [json.loads(r['data']) for r in conn.execute('SELECT data FROM exchange_pending ORDER BY bundle_id')]


def retry_pending(store: ProfileStore, *, recipient: str, trusted_issuer: str, key: bytes) -> list[dict]:
    """Retry the pinned channel only; every retry reauthenticates its envelope."""
    _key(key)
    _profile(recipient)
    _profile(trusted_issuer)
    results = {}
    remaining = [b for b in pending(store) if b['issuer'] == trusted_issuer and b['recipient'] == recipient]
    while remaining:
        next_pass = []
        for bundle in remaining:
            result = apply_bundle(store, bundle, recipient=recipient, trusted_issuer=trusted_issuer, key=key)
            results[bundle['bundle_id']] = result
            if result['status'] == 'pending':
                next_pass.append(bundle)
        if len(next_pass) == len(remaining):
            break
        remaining = next_pass
    return [results[ident] for ident in sorted(results)]


def apply_bundle(store: ProfileStore, bundle: dict, *, recipient: str, trusted_issuer: str, key: bytes) -> dict:
    """Authenticate pinned issuer/recipient, validate, and atomically receive."""
    _authenticate(store, bundle, recipient, trusted_issuer, key)
    for record in bundle['records']:
        _record(record, trusted_issuer, recipient)
    todo = {(r['id'], r['revision']): r for r in bundle['records']}
    if len(todo) != len(bundle['records']):
        raise KnowledgeError('duplicate record identity')
    with store.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='exchange_receipts'").fetchone()
        receipt = conn.execute('SELECT status FROM exchange_receipts WHERE bundle_id=?', (bundle['bundle_id'],)).fetchone() if exists else None
        if receipt:
            return {'status': 'duplicate', 'original_status': receipt['status'], 'bundle_id': bundle['bundle_id']}
        roots = {}
        for record in todo.values():
            ident = record['id']
            if not record['parents']:
                if ident in roots and roots[ident] != record['revision']:
                    raise KnowledgeError('multiple roots for an identity')
                roots[ident] = record['revision']
                existing_roots = conn.execute('SELECT data FROM revisions WHERE id=?', (ident,)).fetchall()
                if any(not json.loads(row['data'])['parents'] and json.loads(row['data'])['revision'] != record['revision'] for row in existing_roots):
                    raise KnowledgeError('independent root for existing identity')
            for parent in record['parents']:
                old = todo.get((ident, parent))
                if old is None:
                    row = store.raw(ident, parent, conn)
                    old = json.loads(row['data']) if row else None
                if old and any(record[f] != old[f] for f in ('id', 'owner', 'project', 'kind', 'derived_from')):
                    raise KnowledgeError('immutable ancestry header changed')
        missing = any(not store.raw(r['id'], p, conn) and (r['id'], p) not in todo for r in todo.values() for p in r['parents'])
        for record in todo.values():
            for ref in record['derived_from']:
                if ref['owner'] != trusted_issuer:
                    raise AccessDenied('cross-owner dependency cannot be authenticated by this channel')
                dependency = todo.get((ref['id'], ref['revision']))
                if dependency is None:
                    row = store.raw(ref['id'], ref['revision'], conn)
                    dependency = json.loads(row['data']) if row else None
                if dependency is None:
                    missing = True
                else:
                    _allowed(dependency, recipient)
                    candidates = {row['revision']: json.loads(row['data']) for row in conn.execute(
                        'SELECT r.revision,r.data FROM heads h JOIN revisions r USING(id,revision) WHERE h.id=?', (ref['id'],))}
                    incoming = [r for r in todo.values() if r['id'] == ref['id'] and not store.raw(r['id'], r['revision'], conn)]
                    candidates.update({r['revision']: r for r in incoming})
                    for revision in {p for r in incoming for p in r['parents']}:
                        candidates.pop(revision, None)
                    if len(candidates) != 1 or any(r['status'] != 'active' for r in candidates.values()):
                        raise AccessDenied('derived dependency is conflicted or revoked')
                    _allowed(next(iter(candidates.values())), recipient)
        _tables(conn)
        if missing:
            data = canonical(bundle)
            existing = conn.execute('SELECT data FROM exchange_pending WHERE bundle_id=?', (bundle['bundle_id'],)).fetchone()
            if existing:
                if existing['data'] != data:
                    raise KnowledgeError('pending envelope collision')
            else:
                count, size = conn.execute('SELECT count(*), coalesce(sum(length(CAST(data AS BLOB))),0) FROM exchange_pending').fetchone()
                if count >= 128 or size + len(data.encode('utf-8')) > MAX_BYTES:
                    raise KnowledgeError('pending queue quota exceeded')
                conn.execute('INSERT INTO exchange_pending VALUES(?,?)', (bundle['bundle_id'], data))
            conn.executemany('INSERT OR IGNORE INTO exchange_pending_blocks VALUES(?,?)',
                             [(record['id'], bundle['bundle_id']) for record in bundle['records']])
            return {'status': 'pending', 'bundle_id': bundle['bundle_id']}
        while todo:
            ready = [r for r in todo.values() if all(store.raw(r['id'], p, conn) for p in r['parents'])]
            if not ready:
                raise KnowledgeError('missing ancestry')
            for record in ready:
                existing = store.raw(record['id'], record['revision'], conn)
                if existing:
                    if existing['data'] != canonical(record):
                        raise KnowledgeError('revision collision')
                else:
                    store.put(record, recipient, conn)
                del todo[(record['id'], record['revision'])]
        status = 'applied'
        for ident in {r['id'] for r in bundle['records']}:
            if conn.execute('SELECT count(*) FROM heads WHERE id=?', (ident,)).fetchone()[0] > 1:
                status = 'conflict'
        conn.execute('DELETE FROM exchange_pending WHERE bundle_id=?', (bundle['bundle_id'],))
        conn.execute('INSERT INTO exchange_receipts VALUES(?,?,?,?)', (bundle['bundle_id'], trusted_issuer, recipient, status))
    return {'status': status, 'bundle_id': bundle['bundle_id']}
