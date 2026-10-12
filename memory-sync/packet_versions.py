"""Permanent legacy readers plus read-only resolution-format adapters.

No packet constructor/signing/emission API. New packets are retained as pending
by receive; transactional application is deliberately a later gate.
"""
import hashlib
import json

import memory_sync as sync
import signed_packets as signed
import resolution_protocol as contract
import resolution_store as store

LEGACY_UNSIGNED = 'omp-memory-changes-v1'
LEGACY_SIGNED = 'agentmesh-signed-changes-v2'
NEW_UNSIGNED = contract.UNSIGNED_FORMAT
NEW_SIGNED = contract.SIGNED_FORMAT
SEMANTIC_FIELDS = {'group', 'node', 'uuid', 'kind', 'body', 'covers', 'evidence'}
UNSIGNED_FIELDS = SEMANTIC_FIELDS | {'format', 'checksum'}
UNSIGNED_DOMAIN = b'AgentMesh/change-packet/unsigned/v2\x00'
LEGACY_UNSIGNED_DOMAIN = b'AgentMesh/change-packet/unsigned/v1\x00'


def is_new(p):
    return isinstance(p, dict) and p.get('format') in (NEW_UNSIGNED, NEW_SIGNED)


def semantic(p):
    return {k: p[k] for k in SEMANTIC_FIELDS}


def envelope_identity(envelope) -> str:
    fmt = envelope.get('format')
    if fmt in (LEGACY_SIGNED, NEW_SIGNED):
        return signed.envelope_digest(envelope)
    if fmt == NEW_UNSIGNED:
        return hashlib.sha256(UNSIGNED_DOMAIN + signed.typed(envelope)).hexdigest()
    if fmt == LEGACY_UNSIGNED:
        return hashlib.sha256(LEGACY_UNSIGNED_DOMAIN + sync.canonical(envelope).encode('utf-8')).hexdigest()
    raise ValueError('unsupported packet format')


def receipt_identity(envelope) -> str:
    if envelope.get('format') == LEGACY_UNSIGNED:
        return envelope['checksum']  # historical receipts bind only body, forever
    if envelope.get('format') == NEW_UNSIGNED:
        return envelope_identity(envelope)
    if envelope.get('format') in (LEGACY_SIGNED, NEW_SIGNED):
        return signed.receipt_digest(envelope)
    raise ValueError('unsupported packet format')


def validate_wire(envelope, *, group, folder) -> None:
    """Versioned syntax/checksums/scope, without a trust or SQL decision."""
    if type(envelope) is not dict: raise ValueError('invalid envelope')
    fmt = envelope.get('format')
    if fmt in (LEGACY_SIGNED, NEW_SIGNED):
        signed.validate_envelope(envelope, group, folder)
        if fmt == NEW_SIGNED: signed.wire(envelope)
        return
    if fmt == LEGACY_UNSIGNED:
        if set(envelope) != {'format','group','node','uuid','body','checksum'}:
            raise ValueError('invalid legacy envelope')
        if envelope['group'] != group or envelope['node'] not in sync.RANGES or not sync.valid_uuid(envelope['uuid']):
            raise ValueError('invalid legacy scope')
        if not isinstance(envelope['body'], list) or not envelope['body'] or sync.digest(envelope['body']) != envelope['checksum']:
            raise ValueError('legacy checksum/shape mismatch')
        return
    if fmt != NEW_UNSIGNED or set(envelope) != UNSIGNED_FIELDS:
        raise ValueError('unsupported packet format or fields')
    signed.wire(envelope)
    if envelope['group'] != group or envelope['node'] != folder:
        raise ValueError('invalid new packet scope')
    payload = semantic(envelope)
    contract.packet_from_payload(payload, envelope_digest='0'*64)
    if envelope['checksum'] != contract.payload_digest(payload):
        raise ValueError('semantic checksum mismatch')


def _validate_rows(c, p):
    # Reuse v1 schema/type validation only. Never pass this projection to _apply.
    body = []
    for ch in p['body']:
        raw = {k: ch[k] for k in ('table','key','row','revision')}
        raw['parent'] = ch['parents'][0] if ch['parents'] else None
        body.append(raw)
    projected = {k:p[k] for k in ('group','node','uuid')}
    projected.update(format=LEGACY_UNSIGNED, body=body, checksum=sync.digest(body))
    sync._validate(c, projected)


def _authorize(c, p, security, folder):
    complete = envelope_identity(p)
    disposition = []
    if store.exists(c):
        disposition = c.execute('SELECT envelope_digest FROM _sync_packet_dispositions WHERE packet_uuid=?', (p['uuid'],)).fetchall()
    if disposition:
        if any(row[0] != complete for row in disposition):
            raise ValueError('superseded packet envelope changed')
        return store.Authorization(complete, 'superseded')
    receipt = c.execute('SELECT checksum FROM _sync_receipts WHERE uuid=?', (p['uuid'],)).fetchone()
    if receipt:
        if receipt[0] != receipt_identity(p): raise ValueError('packet UUID reused')
        if security is not None:
            sync._received_signed(c, p, security, folder)
        return store.Authorization(complete, 'applied')
    if security is not None:
        security.verify(p, sync.config(c)['group_id'], folder)
    if p['node'] == sync.config(c)['node']:
        own = c.execute('SELECT packet FROM _sync_outbox WHERE uuid=?', (p['uuid'],)).fetchone()
        if not own or sync.load_packet(own[0]) != p:
            raise ValueError('unknown packet claiming local node')
        return store.Authorization(complete, 'local')
    return store.Authorization(complete, 'verified')


def _has_committed_envelope(c, ident):
    """Telemetry hint only. Honor only AFTER decode validates the exact identity.

    Old receipts without pins still require historical signature verification.
    This hint never authorizes a packet or skips any decoder checks.
    """
    if not signed.canonical_uuid(ident):
        return False
    if c.execute('''SELECT 1 FROM _sync_receipts r JOIN _sync_receipt_envelopes e
                    ON r.uuid=e.uuid WHERE r.uuid=?''', (ident,)).fetchone():
        return True
    return store.exists(c) and bool(c.execute(
        'SELECT 1 FROM _sync_packet_dispositions WHERE packet_uuid=?', (ident,)).fetchone())


def decode_packet(c, envelope, *, security, folder, filename) -> contract.Packet:
    """Validate, authorize, retain and normalize evidence; never apply rows.

    Missing legacy baseline witnesses can raise MissingEvidence AFTER retention.
    A caller may keep that evidence and retry, without inventing origin payloads.
    """
    with store.atomic(c):
        fmt = envelope.get('format') if isinstance(envelope, dict) else None
        if fmt not in (LEGACY_UNSIGNED, LEGACY_SIGNED, NEW_UNSIGNED, NEW_SIGNED):
            raise ValueError('unsupported packet format')
        if (security is not None) != (fmt in (LEGACY_SIGNED, NEW_SIGNED)):
            raise ValueError('signed policy/packet mismatch')
        sync._security_policy(c, security)
        validate_wire(envelope, group=sync.config(c)['group_id'], folder=folder)
        # Preserve v1's historical duplicate-file behavior; new and signed formats bind filenames.
        if fmt != LEGACY_UNSIGNED and filename != envelope['uuid']+'.json':
            raise ValueError('packet filename mismatch')
        if is_new(envelope):
            _validate_rows(c, envelope)
        else:
            legacy = {k: envelope[k] for k in ('group','node','uuid','body')}
            legacy.update(format=LEGACY_UNSIGNED, checksum=sync.digest(envelope['body']))
            sync._validate(c, legacy)
        authorization = _authorize(c, envelope, security, folder)
        ref = store.retain_evidence(c, envelope, authorization)
    if is_new(envelope):
        return contract.packet_from_payload(semantic(envelope), envelope_digest=ref.digest)
    keys = [contract.Key(ch['table'], tuple(ch['key'])) for ch in envelope['body']]
    origins = store.load_origins(c, keys)
    changes = []
    for ch, key in zip(envelope['body'], keys):
        node = c.execute('SELECT origin,evidence_state FROM _sync_revision_nodes WHERE revision=?',
                         (ch['revision'],)).fetchone()
        if node is None or node['evidence_state'] == 'unresolved':
            raise contract.MissingEvidence('legacy ancestry evidence unavailable')
        if node['origin'] != origins[key]:
            raise ValueError('legacy origin mismatch')
        changes.append(contract.Change(key, ch['revision'], () if ch['parent'] is None else (ch['parent'],),
                                       origins[key], sync.canonical(ch['row']) if ch['row'] is not None else None))
    changes.sort(key=lambda ch: (ch.key.table, sync.canonical(list(ch.key.values))))
    return contract.Packet(ref, 'change', tuple(changes), (), (), envelope['group'], envelope['node'])
