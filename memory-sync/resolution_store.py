"""Private evidence schema for resolution readers. Never applies application rows.

Migrations and evidence indexing use savepoints: they are atomic independently,
and never commit a caller's transaction. Retained evidence is not authorization.
"""
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
import json

import memory_sync as sync
from resolution_protocol import Key, Ref, Limits, MissingEvidence

SCHEMA_VERSION = 1
DDL = (
    'CREATE TABLE _sync_protocol(id INTEGER PRIMARY KEY CHECK(id=1),schema_version INTEGER NOT NULL,emission_enabled INTEGER NOT NULL DEFAULT 0 CHECK(emission_enabled IN (0,1)),activation_manifest_digest TEXT)',
    'CREATE TABLE _sync_revision_nodes(revision TEXT PRIMARY KEY,table_name TEXT NOT NULL,key TEXT NOT NULL,origin TEXT,row_digest TEXT NOT NULL,row_json TEXT,packet_uuid TEXT NOT NULL,evidence_state TEXT NOT NULL)',
    'CREATE TABLE _sync_revision_parents(revision TEXT NOT NULL,parent TEXT NOT NULL,PRIMARY KEY(revision,parent))',
    'CREATE TABLE _sync_packet_evidence(uuid TEXT PRIMARY KEY,envelope_digest TEXT NOT NULL,envelope_json TEXT NOT NULL,format TEXT NOT NULL,sender TEXT,key_id TEXT,authorization_state TEXT NOT NULL)',
    "CREATE TABLE _sync_packet_dispositions(packet_uuid TEXT NOT NULL,envelope_digest TEXT NOT NULL,resolution_uuid TEXT NOT NULL,disposition TEXT NOT NULL CHECK(disposition='superseded'),PRIMARY KEY(packet_uuid,resolution_uuid))",
    'CREATE TABLE _sync_origins(table_name TEXT NOT NULL,key TEXT NOT NULL,revision TEXT,row_json TEXT,PRIMARY KEY(table_name,key))',
    'CREATE INDEX _sync_revision_parent_lookup ON _sync_revision_parents(parent)',
)


@contextmanager
def atomic(c):
    c.execute('SAVEPOINT resolution_store')
    try:
        yield
    except BaseException:
        c.execute('ROLLBACK TO resolution_store')
        c.execute('RELEASE resolution_store')
        raise
    else:
        c.execute('RELEASE resolution_store')


def exists(c):
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE name='_sync_protocol'").fetchone())


def _baseline(table, key, row):
    if row is None:
        raise ValueError('missing baseline row witness')
    return 'baseline:' + sync.digest([table, json.loads(key), json.loads(row)])


def migrate(c) -> None:
    """Explicit idempotent migration; no replay, activation or history fabrication."""
    with atomic(c):
        sync.config(c)
        if exists(c):
            version = c.execute('SELECT schema_version FROM _sync_protocol WHERE id=1').fetchone()
            if version is None or version[0] != SCHEMA_VERSION:
                raise ValueError('unsupported resolution schema')
            return
        for sql in DDL:
            c.execute(sql)
        for table, key, row, revision in c.execute("SELECT * FROM _sync_shadow WHERE revision LIKE 'baseline:%'"):
            if _baseline(table, key, row) != revision:
                raise ValueError('baseline witness mismatch')
            c.execute('INSERT INTO _sync_origins VALUES(?,?,?,?)', (table, key, revision, row))
        c.execute('INSERT INTO _sync_protocol VALUES(1,?,0,NULL)', (SCHEMA_VERSION,))


def load_origins(c, keys) -> dict:
    """Read verified baseline witnesses/creation roots, never guess missing roots.

    Origin bytes are not a trust decision for packets establishing those roots;
    callers must reauthorize their evidence with packet_versions.decode_packet.
    """
    result = {}
    for key in keys:
        encoded = sync.canonical(list(key.values))
        row = c.execute('SELECT revision,row_json FROM _sync_origins WHERE table_name=? AND key=?',
                        (key.table, encoded)).fetchone()
        if row is None:
            raise MissingEvidence('origin witness unavailable')
        rev, value = row
        if rev is not None:
            if _baseline(key.table, encoded, value) != rev:
                raise ValueError('baseline witness mismatch')
        elif value is not None or not c.execute('''SELECT 1 FROM _sync_revision_nodes n
                WHERE table_name=? AND key=? AND NOT EXISTS
                (SELECT 1 FROM _sync_revision_parents p WHERE p.revision=n.revision)''', (key.table, encoded)).fetchone():
            raise ValueError('absence witness unavailable')
        result[key] = rev
    return result


@dataclass(frozen=True)
class Authorization:
    """Adapter result bound to this envelope; not a durable trust cache."""
    envelope_digest: str
    state: str


def retain_evidence(c, envelope, authorization: Authorization) -> Ref:
    """Caller has authenticated and schema-validated this exact envelope.

    No state here grants a historical exception on its own. Receipts, outbox or
    dispositions are checked again by the adapter on every decode.
    """
    from packet_versions import envelope_identity, is_new
    complete = envelope_identity(envelope)
    if not isinstance(authorization, Authorization) or authorization.envelope_digest != complete or authorization.state not in ('verified', 'applied', 'superseded', 'local'):
        raise ValueError('invalid evidence authorization')
    with atomic(c):
        migrate(c)
        previous = c.execute('SELECT envelope_digest,envelope_json FROM _sync_packet_evidence WHERE uuid=?', (envelope['uuid'],)).fetchone()
        encoded = sync.canonical(envelope)
        if previous and tuple(previous) != (complete, encoded):
            raise ValueError('packet UUID reused')
        if not previous:
            c.execute('INSERT INTO _sync_packet_evidence VALUES(?,?,?,?,?,?,?)',
                      (envelope['uuid'], complete, encoded, envelope['format'], envelope.get('sender'), envelope.get('key_id'), authorization.state))
        elif authorization.state != 'verified':
            c.execute('UPDATE _sync_packet_evidence SET authorization_state=? WHERE uuid=?', (authorization.state, envelope['uuid']))
        starts = []
        for ch in envelope['body']:
            table, key, revision = ch['table'], sync.canonical(ch['key']), ch['revision']
            parents = ch['parents'] if is_new(envelope) else ([] if ch['parent'] is None else [ch['parent']])
            row = sync.canonical(ch['row']) if ch['row'] is not None else None
            stored = c.execute('SELECT table_name,key,row_digest,row_json,packet_uuid FROM _sync_revision_nodes WHERE revision=?', (revision,)).fetchone()
            definition = (table, key, sync.digest(ch['row']), row, envelope['uuid'])
            if stored and tuple(stored) != definition:
                raise ValueError('revision reused')
            historical = c.execute('SELECT table_name,key FROM _sync_history WHERE revision=?', (revision,)).fetchone()
            if historical and (tuple(historical) != (table, key) or (not stored and authorization.state not in ('applied', 'local', 'superseded'))):
                raise ValueError('revision identity has no matching committed packet')
            if not stored:
                c.execute('INSERT INTO _sync_revision_nodes VALUES(?,?,?,?,?,?,?,?)',
                          (revision, table, key, None, definition[2], row, envelope['uuid'], 'unresolved'))
                for parent in parents:
                    c.execute('INSERT INTO _sync_revision_parents VALUES(?,?)', (revision, parent))
            elif {r[0] for r in c.execute('SELECT parent FROM _sync_revision_parents WHERE revision=?', (revision,))} != set(parents):
                raise ValueError('revision parents changed')
            starts.append(revision)
        _refresh(c, starts)
    return Ref(envelope['uuid'], complete)


def _refresh(c, starts):
    """Reconstruct only the affected component, including delayed predecessors.

    Roots without a retained baseline payload remain unresolved. Neither these
    nodes nor unmaterialized branch nodes enter the legacy _sync_history ledger.
    """
    from packet_versions import is_new
    limit = Limits().revisions
    affected, queue = set(), deque(starts)
    while queue:
        revision = queue.popleft()
        if revision in affected: continue
        affected.add(revision)
        if len(affected) > limit: raise ValueError('evidence component limit')
        queue.extend(r[0] for r in c.execute('SELECT revision FROM _sync_revision_parents WHERE parent=?', (revision,)))
    nodes, edges, envelopes = {}, {}, {}
    queue = deque(affected)
    while queue:
        revision = queue.popleft()
        if revision in nodes: continue
        row = c.execute('SELECT * FROM _sync_revision_nodes WHERE revision=?', (revision,)).fetchone()
        if row is None: continue  # a future packet can supply this parent
        nodes[revision] = dict(row)
        if len(nodes) > limit: raise ValueError('evidence component limit')
        edges[revision] = [r[0] for r in c.execute('SELECT parent FROM _sync_revision_parents WHERE revision=?', (revision,))]
        queue.extend(parent for parent in edges[revision] if not parent.startswith('baseline:'))
        ident = row['packet_uuid']
        if ident not in envelopes:
            raw, state = c.execute('SELECT envelope_json,authorization_state FROM _sync_packet_evidence WHERE uuid=?', (ident,)).fetchone()
            envelopes[ident] = (json.loads(raw), state)
    children = {r: [] for r in nodes}; degrees = {}; roots = {}
    unknown = object()
    for rev, node in nodes.items():
        degrees[rev] = 0
        for parent in edges[rev]:
            if parent in nodes:
                other = nodes[parent]
                if (other['table_name'], other['key']) != (node['table_name'], node['key']):
                    raise ValueError('cross-key revision parent')
                children[parent].append(rev); degrees[rev] += 1
    queue = deque(r for r, degree in degrees.items() if degree == 0)
    while queue:
        rev = queue.popleft(); node = nodes[rev]
        candidates = [parent if parent.startswith('baseline:') else roots.get(parent, unknown) for parent in edges[rev]]
        known = {root for root in candidates if root is not unknown}
        if len(known) > 1: raise ValueError('baseline or origin mismatch')
        origin = unknown if any(root is unknown for root in candidates) else next(iter(known), None)
        roots[rev] = origin
        if origin is not unknown:
            envelope, state = envelopes[node['packet_uuid']]
            if is_new(envelope):
                declaration = next(ch for ch in envelope['body'] if ch['revision'] == rev)
                if declaration['origin'] != origin: raise ValueError('declared origin mismatch')
            baseline_history = {r[0] for r in c.execute("SELECT revision FROM _sync_history WHERE table_name=? AND key=? AND revision LIKE 'baseline:%'", (node['table_name'], node['key']))}
            if baseline_history and baseline_history != {origin}:
                raise ValueError('baseline history mismatch')
            old = c.execute('SELECT revision FROM _sync_origins WHERE table_name=? AND key=?', (node['table_name'], node['key'])).fetchone()
            if old and old[0] != origin: raise ValueError('origin witness mismatch')
            if not edges[rev] and old is None:
                c.execute('INSERT INTO _sync_origins VALUES(?,?,NULL,NULL)', (node['table_name'], node['key']))
            try:
                load_origins(c, [Key(node['table_name'], tuple(json.loads(node['key'])))])
            except MissingEvidence:
                state = 'unresolved'
            c.execute('UPDATE _sync_revision_nodes SET origin=?,evidence_state=? WHERE revision=?', (origin, state, rev))
        for child in children[rev]:
            degrees[child] -= 1
            if degrees[child] == 0: queue.append(child)
    if len(roots) != len(nodes): raise ValueError('cyclic revision evidence')


def load_evidence(c, refs) -> dict:
    """Return exact retained envelopes, NOT authorized Packet objects.

    Every caller must decode/reauthorize these again before relying on evidence.
    This API has no security argument and deliberately cannot promise current trust.
    """
    from packet_versions import envelope_identity
    refs = tuple(refs)
    if len(refs) > Limits().packets: raise ValueError('evidence reference limit')
    result = {}
    for ref in refs:
        row = c.execute('SELECT envelope_digest,envelope_json FROM _sync_packet_evidence WHERE uuid=?', (ref.uuid,)).fetchone()
        if row is None: raise MissingEvidence('packet evidence unavailable')
        if row[0] != ref.digest: raise ValueError('evidence digest mismatch')
        envelope = sync.load_packet(row[1])
        if envelope['uuid'] != ref.uuid or envelope_identity(envelope) != ref.digest:
            raise ValueError('stored evidence changed')
        result[ref.uuid] = envelope
    return result
