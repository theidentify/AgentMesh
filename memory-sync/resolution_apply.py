"""Atomic resolution application. Authoring requires an operator-approved activation record.

CLI confirmation plans bind prepare/commit. Retained envelopes are reauthorized on
use; only exact committed receipts/dispositions survive later revocation.
"""
from dataclasses import dataclass
import json
import sqlite3
import uuid

import memory_sync as sync
import packet_versions as versions
import resolution_protocol as protocol
import resolution_store as store
import signed_packets as signed


@dataclass(frozen=True)
class ResolutionPlan:
    payload_json: str
    state_digest: str
    trust_digest: str
    schema_version: int
    digest: str


@dataclass(frozen=True)
class ApplyResult:
    status: str  # applied, superseded, noop
    packet_uuid: str


def _checkpoint(boundary):
    """Inert fault-injection seam; never controls authorization or activation."""


def _require_activation(c):
    from protocol_activation import emission_enabled
    if not emission_enabled(c): raise ValueError('new packet emission disabled')


def _trust(security):
    return sync.digest([security.public,signed.read_trust(security.directory)]) if security else sync.digest(None)


def _state(c):
    return sync.digest([sync.config(c), sorted((t,k,v) for (t,k),v in sync.rows(c).items()),
                        [tuple(r) for r in c.execute('SELECT * FROM _sync_shadow ORDER BY table_name,key')],
                        [tuple(r) for r in c.execute('SELECT * FROM _sync_protocol')]])


def _decode(c, raw, security):
    return versions.decode_packet(c,raw,security=security,folder=raw['node'],filename=raw['uuid']+'.json')


def _index_local(c, security):
    # No row scan/capture here. Old immutable outboxes are a source of proof.
    for row in c.execute('SELECT packet FROM _sync_outbox').fetchall():
        raw = sync.load_packet(row[0])
        retained=c.execute('SELECT envelope_digest FROM _sync_packet_evidence WHERE uuid=?',(raw['uuid'],)).fetchone()
        if retained and retained[0]==versions.envelope_identity(raw): continue
        try: _decode(c,raw,security)
        except protocol.MissingEvidence: pass


def _owner(c, revision):
    row = c.execute('''SELECT e.uuid,e.envelope_digest FROM _sync_revision_nodes n
        JOIN _sync_packet_evidence e ON e.uuid=n.packet_uuid WHERE n.revision=?''',(revision,)).fetchone()
    if row is None: raise protocol.MissingEvidence('current or parent evidence unavailable')
    return protocol.Ref(*row)


def _graph(c, refs, security):
    """Bounded transitive packet closure, reauthorized at every use."""
    waiting=list(refs); raw={}; identities={}
    while waiting:
        ref=waiting.pop()
        if ref.uuid in identities:
            if identities[ref.uuid] != ref: raise ValueError('evidence digest mismatch')
            continue
        if len(raw) >= protocol.Limits().packets-1: raise ValueError('packet closure limit')
        envelope=store.load_evidence(c,[ref])[ref.uuid]
        # Authentication cannot wait for graph normalization.
        try: _decode(c,envelope,security)
        except protocol.MissingEvidence: pass
        identities[ref.uuid]=ref; raw[ref.uuid]=envelope
        if versions.is_new(envelope):
            waiting.extend(protocol.Ref(r['uuid'],r['envelope_digest']) for r in envelope['evidence'])
        for ch in envelope['body']:
            parents=ch['parents'] if versions.is_new(envelope) else [ch['parent']]
            for parent in parents:
                if parent is not None and not parent.startswith('baseline:'):
                    waiting.append(_owner(c,parent))
    return {ident:_decode(c,envelope,security) for ident,envelope in raw.items()}


def _heads(c, keys):
    result={}
    for key in keys:
        row=c.execute('SELECT revision FROM _sync_shadow WHERE table_name=? AND key=?',
                      (key.table,sync.canonical(list(key.values)))).fetchone()
        result[key]=row[0] if row else None
    return result


def prepare_resolution(c, source_refs, choices, *, security):
    _require_activation(c)
    with store.atomic(c):
        sync._security_policy(c,security)
        _index_local(c,security)
        packets=_graph(c,source_refs,security)
        changes={ch.revision:ch for p in packets.values() for ch in p.changes}
        keys={ch.key for ch in changes.values()}
        if set(choices)!=keys: raise ValueError('choices must cover every packet key')
        origins=store.load_origins(c,keys)
        parents={parent for ch in changes.values() for parent in ch.parents}
        body=[]
        for key in sorted(keys,key=lambda k:(k.table,sync.canonical(list(k.values)))):
            frontier=sorted(rev for rev,ch in changes.items() if ch.key==key and rev not in parents)
            body.append(dict(table=key.table,key=list(key.values),row=choices[key],
                             revision=str(uuid.uuid4()),parents=frontier,origin=origins[key]))
        refs=[dict(uuid=p.ref.uuid,envelope_digest=p.ref.digest) for p in sorted(packets.values(),key=lambda p:p.ref.uuid)]
        cfg=sync.config(c)
        payload=dict(group=cfg['group_id'],node=cfg['node'],uuid=str(uuid.uuid4()),kind='resolution',
                     body=body,covers=refs,evidence=refs)
        normalized=protocol.packet_from_payload(payload,envelope_digest='0'*64)
        protocol.validate_resolution(normalized,packets,origins)
        # Full row schema/type validation without applying or signing.
        versions._validate_rows(c,payload)
        encoded=sync.canonical(payload); state=_state(c); trust=_trust(security)
        digest=sync.digest([encoded,state,trust,store.SCHEMA_VERSION])
        return ResolutionPlan(encoded,state,trust,store.SCHEMA_VERSION,digest)


def _write(c, transitions):
    """Install a complete vector, retaining newer proved heads. No relational repair."""
    keys={(t.key.table,sync.canonical(list(t.key.values))) for t in transitions}
    current=sync._keyed_rows(c,keys)
    expected=sync._shadow_rows(c,keys)
    if current != expected: raise ValueError('uncaptured local edits')
    installs=[t for t in transitions if t.action=='install']
    try:
        with sync._track_writes(c):
            c.execute('UPDATE _sync_config SET importing=1')
            _install_rows(c,installs,expected,keys)
    except sqlite3.IntegrityError as exc:
        raise ValueError('unsupported relational repair') from exc
    _finish_write(c,transitions,installs)


def _install_rows(c,installs,expected,keys):
    for t in sorted(installs,key=lambda t:(t.key.table!='memory_sources',t.key.table=='observation_events')):
        table=t.key.table; key=sync.canonical(list(t.key.values))
        if t.row_json is None:
            c.execute('DELETE FROM '+table+' WHERE '+' AND '.join(k+'=?' for k in sync.KEYS[table]),t.key.values)
            expected.pop((table,key),None)
    for t in installs:
        if t.row_json is None: continue
        table=t.key.table; row=json.loads(t.row_json); columns=list(row)
        update=','.join(k+'=excluded.'+k for k in columns if k not in sync.KEYS[table])
        c.execute('INSERT INTO '+table+' ('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+') ON CONFLICT ('+','.join(sync.KEYS[table])+') DO '+('UPDATE SET '+update if update else 'NOTHING'),
                  [sync.canonical(row[k]) if k=='metadata' else row[k] for k in columns])
        expected[table,sync.canonical(list(t.key.values))]=row
    allowed={(t.key.table,sync.canonical(list(t.key.values))) for t in installs}
    expected={key:expected.get(key) for key in keys}
    if not sync._verify_writes(c,allowed,expected):
        raise protocol.ResolutionConflict('unsupported relational repair')
    if c.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('unsupported relational repair')


def _finish_write(c,transitions,installs):
    _checkpoint('rows')
    for t in installs:
        key=sync.canonical(list(t.key.values))
        c.execute('INSERT OR REPLACE INTO _sync_shadow VALUES(?,?,?,?)',(t.key.table,key,t.row_json,t.revision))
    _checkpoint('shadow')
    for t in transitions:
        key=sync.canonical(list(t.key.values))
        old=c.execute('SELECT table_name,key FROM _sync_history WHERE revision=?',(t.revision,)).fetchone()
        if old and tuple(old)!=(t.key.table,key): raise ValueError('revision reused')
        c.execute('INSERT OR IGNORE INTO _sync_history VALUES(?,?,?)',(t.revision,t.key.table,key))
    _checkpoint('history')
    c.execute('UPDATE _sync_config SET importing=0')


def _clear(c, ident, digest):
    c.execute('''DELETE FROM _sync_diagnostics WHERE path IN
        (SELECT path FROM _sync_packet_paths WHERE packet_uuid=? AND envelope_digest=?)''',(ident,digest))


def apply_resolution(c, packet, *, security):
    """Revalidate and atomically apply an envelope, never a caller-trusted plan."""
    with store.atomic(c):
        trust_before=_trust(security)
        normalized=_decode(c,packet,security)
        auth=versions._authorize(c,packet,security,packet['node'])
        if auth.state in ('applied','superseded') or (auth.state=='local' and normalized.kind=='change'):
            _clear(c,normalized.ref.uuid,normalized.ref.digest)
            return ApplyResult('superseded' if auth.state=='superseded' else 'noop',normalized.ref.uuid)
        _index_local(c,security)
        heads=_heads(c,[ch.key for ch in normalized.changes])
        if normalized.kind=='resolution':
            refs=list(normalized.evidence)
            for head in heads.values():
                if head is not None and not head.startswith('baseline:'):
                    refs.append(_owner(c,head))
            packets=_graph(c,refs,security)
            keys={ch.key for p in [*packets.values(),normalized] for ch in p.changes}
            proof=protocol.validate_resolution(normalized,packets,store.load_origins(c,keys))
            transitions=protocol.plan_resolution(proof,heads)
        else:
            # Ordinary new-format changes retain exact-parent whole-packet semantics.
            _graph(c,[normalized.ref],security)
            transitions=[]
            for ch in normalized.changes:
                parent=ch.parents[0] if ch.parents else None
                if heads[ch.key]!=parent:
                    if parent is not None and not c.execute('SELECT 1 FROM _sync_history WHERE revision=?',(parent,)).fetchone():
                        raise protocol.MissingEvidence('waiting for ordinary predecessor')
                    raise protocol.ResolutionConflict('ordinary parent mismatch')
                transitions.append(protocol.Transition(ch.key,ch.revision,ch.row_json,'install'))
        _write(c,transitions)
        for ref in normalized.covers:
            c.execute("INSERT OR IGNORE INTO _sync_packet_dispositions VALUES(?,?,?,'superseded')",
                      (ref.uuid,ref.digest,normalized.ref.uuid))
            _clear(c,ref.uuid,ref.digest)
            c.execute("UPDATE _sync_packet_evidence SET authorization_state='superseded' WHERE uuid=?",(ref.uuid,))
            c.execute("UPDATE _sync_revision_nodes SET evidence_state='superseded' WHERE packet_uuid=?",(ref.uuid,))
        _checkpoint('dispositions')
        c.execute('INSERT INTO _sync_receipts VALUES(?,?)',(packet['uuid'],versions.receipt_identity(packet)))
        if security:
            c.execute('INSERT INTO _sync_receipt_envelopes VALUES(?,?)',(packet['uuid'],normalized.ref.digest))
        _clear(c,normalized.ref.uuid,normalized.ref.digest)
        c.execute("UPDATE _sync_packet_evidence SET authorization_state='applied' WHERE uuid=?",(packet['uuid'],))
        c.execute("UPDATE _sync_revision_nodes SET evidence_state='applied' WHERE packet_uuid=?",(packet['uuid'],))
        _checkpoint('receipt')
        if _trust(security)!=trust_before: raise ValueError('trust changed during resolution')
        return ApplyResult('applied' if any(t.action=='install' for t in transitions) else 'noop',normalized.ref.uuid)


def receive_resolution(c, envelope, *, security):
    # Retention precedes the application savepoint; callers can retain proof even
    # when application is pending/conflicted. The receive loop owns diagnostics.
    _decode(c,envelope,security)
    return apply_resolution(c,envelope,security=security)


def commit_resolution(c, plan, *, security, operation_id):
    _require_activation(c)
    if not isinstance(operation_id,str) or not operation_id: raise ValueError('invalid operation ID')
    with store.atomic(c):
        sync._security_policy(c,security)
        if plan.digest != sync.digest([plan.payload_json,plan.state_digest,plan.trust_digest,plan.schema_version]):
            raise ValueError('plan digest mismatch')
        prior=c.execute('SELECT plan_digest,packet_uuid FROM _sync_resolution_operations WHERE operation_id=?',(operation_id,)).fetchone()
        if prior:
            if prior[0]!=plan.digest: raise ValueError('operation ID reused')
            return sync.load_packet(c.execute('SELECT packet FROM _sync_outbox WHERE uuid=?',(prior[1],)).fetchone()[0])
        if plan.schema_version!=store.SCHEMA_VERSION or plan.state_digest!=_state(c) or plan.trust_digest!=_trust(security):
            raise ValueError('stale resolution plan')
        payload=sync.load_packet(plan.payload_json)
        envelope=dict(payload,format=versions.NEW_UNSIGNED,checksum=protocol.payload_digest(payload))
        if security:
            envelope=security._sign_resolution(c,envelope)
        # Insert before decoding so local identity is bound to the exact outbox.
        c.execute('INSERT INTO _sync_outbox(uuid,packet) VALUES(?,?)',(envelope['uuid'],sync.canonical(envelope)))
        _checkpoint('outbox')
        receive_resolution(c,envelope,security=security)
        c.execute('INSERT INTO _sync_resolution_operations VALUES(?,?,?)',(operation_id,plan.digest,envelope['uuid']))
        _checkpoint('operation')
        if _trust(security)!=plan.trust_digest: raise ValueError('trust changed during resolution')
        return envelope


def receive_indexed(c, envelope, path, *, security, diagnostics=None):
    """Receive-loop adapter: retain evidence while rolling back failed row writes."""
    before=c.execute('SELECT count(*) FROM _sync_packet_evidence').fetchone()[0] if store.exists(c) else 0
    missing=None
    try: _decode(c,envelope,security)
    except protocol.MissingEvidence as exc: missing=exc
    c.execute('INSERT OR REPLACE INTO _sync_packet_paths VALUES(?,?,?)',
              (str(path),envelope['uuid'],versions.envelope_identity(envelope)))
    added=c.execute('SELECT count(*) FROM _sync_packet_evidence').fetchone()[0]>before
    # A local writer may commit after receive's initial capture. Preserve that
    # edit under this packet's write lock before any unseen packet can overwrite
    # its row. Exact committed/own replays must not trigger per-packet row scans.
    auth=versions._authorize(c,envelope,security,envelope['node'])
    if auth.state not in ('applied','superseded','local'):
        with store.atomic(c):
            sync._capture(c,security=security,keys=sync._packet_keys(envelope))
            # Retain local proof outside the application savepoint: a pending
            # predecessor must not make the next packet decode this outbox again.
            if versions.is_new(envelope): _index_local(c,security)
    try:
        with store.atomic(c):
            if versions.is_new(envelope):
                if missing: raise missing
                outcome=apply_resolution(c,envelope,security=security).status
            else:
                auth=versions._authorize(c,envelope,security,envelope['node'])
                if auth.state=='superseded': outcome='superseded'
                else:
                    try: outcome='applied' if sync._apply(c,envelope) else 'noop'
                    except sync.Pending:
                        # Covered ancestors deliberately have no fake applied
                        # history entries. A later edit on that replaced branch
                        # is a conflict, not a predecessor that can still arrive.
                        for ch in envelope['body']:
                            covered=c.execute("""SELECT 1 FROM _sync_revision_nodes n
                                JOIN _sync_packet_dispositions d ON d.packet_uuid=n.packet_uuid
                                JOIN _sync_packet_evidence e ON e.uuid=d.packet_uuid AND e.envelope_digest=d.envelope_digest
                                WHERE n.revision=? AND n.table_name=? AND n.key=?""",
                                (ch['parent'],ch['table'],sync.canonical(ch['key']))).fetchone()
                            key=protocol.Key(ch['table'],tuple(ch['key']))
                            if covered and _heads(c,[key])[key]!=ch['parent']:
                                raise protocol.ResolutionConflict('later edit on superseded branch')
                        raise
            c.execute('DELETE FROM _sync_diagnostics WHERE path=?',(str(path),))
            if diagnostics is not None:
                diagnostics.pop(str(path),None)
                # Mirror apply_resolution's covered-path clears only after the
                # entire resolution succeeds, including when receive interrupts.
                if outcome=='applied' and versions.is_new(envelope):
                    for ref in envelope['covers']:
                        for row in c.execute('SELECT path FROM _sync_packet_paths WHERE packet_uuid=? AND envelope_digest=?',
                                             (ref['uuid'],ref['envelope_digest'])):
                            diagnostics.pop(row[0],None)
    except (ValueError,sync.Pending,sync.Conflict,sqlite3.IntegrityError) as exc:
        outcome=('pending' if isinstance(exc,(protocol.MissingEvidence,sync.Pending)) else
                 'conflict' if isinstance(exc,(protocol.ResolutionConflict,sync.Conflict,sqlite3.IntegrityError)) else 'invalid')
        message=type(exc).__name__ if security else str(exc)
        if diagnostics is None:
            c.execute('INSERT OR REPLACE INTO _sync_diagnostics VALUES(?,?,?)',
                      (str(path),outcome,message))
        else: diagnostics[str(path)]=(outcome,message)
    return outcome,added
