"""Gate-2 reader tests: new-format fixtures are signed here, never by an emitter."""
import copy
import hashlib
import importlib
import json
import sqlite3
import uuid

import pytest
import memory_sync as sync
import signed_packets as signed
import resolution_protocol as contract
from test_signed_packets import secure_peers, packet as legacy_signed_packet
from test_memory_sync import GROUP

V3_DOMAIN = b'AgentMesh/change-packet/Ed25519/v3\x00'
UNSIGNED_DOMAIN = b'AgentMesh/change-packet/unsigned/v2\x00'


@pytest.fixture
def versions():
    return importlib.import_module('packet_versions')


@pytest.fixture
def store():
    return importlib.import_module('resolution_store')


def new_packet(security=None, *, kind='change'):
    ident = lambda: str(uuid.uuid4())
    body = [{'table': 'summary_state', 'key': ['gate2'], 'row': {'consumer': 'gate2', 'last_event_id': 7, 'updated_at': 'fixture'},
             'revision': ident(), 'parents': [], 'origin': None}]
    refs = []
    if kind == 'resolution':
        body[0]['parents'] = [ident()]
        refs = [{'uuid': ident(), 'envelope_digest': 'b'*64}]
    payload = {'group': GROUP, 'node': 'mac', 'uuid': ident(), 'kind': kind,
               'body': body, 'covers': refs, 'evidence': refs}
    p = dict(payload, format=contract.UNSIGNED_FORMAT, checksum=contract.payload_digest(payload))
    if security:
        p.update(format=contract.SIGNED_FORMAT, encoding=signed.ENCODING,
                 sender=security.public['sender'], key_id=security.public['key_id'])
        p['signature'] = security.key.sign(V3_DOMAIN + signed.typed(p)).hex()
    return p


def publish_fixture(ex, p):
    namespace = 'signed-changes' if 'signature' in p else 'changes'
    path = ex/namespace/p['node']/(p['uuid']+'.json')
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(signed.wire(p))
    return path


def decode(versions, c, p, security=None):
    return versions.decode_packet(c, p, security=security, folder=p['node'], filename=p['uuid']+'.json')


def test_legacy_fixtures_remain_readable_forever(versions, secure_peers):
    a, b, ex, sa, sb = secure_peers
    path, old = legacy_signed_packet(secure_peers)
    assert old['format'] == signed.FORMAT == 'agentmesh-signed-changes-v2'
    expected = hashlib.sha256(signed.DOMAIN + signed.typed({k:v for k,v in old.items() if k != 'signature'})).hexdigest()
    assert versions.receipt_identity(old) == expected
    assert sync.receive(b, ex, security=sb)['applied'] == 1
    with sync.connect(b) as c, c:
        c.execute('BEGIN IMMEDIATE')
        # Missing old baseline payload is never invented, even for a real receipt.
        with pytest.raises(contract.MissingEvidence): decode(versions, c, old, sb)
    with sync.connect(a) as c:
        legacy = json.loads(c.execute('SELECT packet FROM _sync_outbox').fetchone()[0])
    assert legacy == old
    unsigned = {k:old[k] for k in ('group','node','uuid','body')}
    unsigned.update(format=sync.FORMAT, checksum=sync.digest(unsigned['body']))
    versions.validate_wire(unsigned, group=GROUP, folder='mac')
    assert versions.receipt_identity(unsigned) == unsigned['checksum']
    assert sync.capture(a, security=sa)['changes'] == 0


@pytest.mark.parametrize('secure', [False, True])
@pytest.mark.parametrize('kind', ['change', 'resolution'])
def test_new_packet_read_does_not_emit_or_apply(versions, store, secure_peers, secure, kind):
    a, b, ex, sa, sb = secure_peers
    p = new_packet(sa if secure else None, kind=kind)
    path = publish_fixture(ex, p)
    with sync.connect(b) as c:
        before = sync.rows(c); shadow = list(c.execute('SELECT * FROM _sync_shadow'))
    report = sync.receive(b, ex, security=sb if secure else None)
    assert report['pending'] == 1 and report['applied'] == report['invalid'] == 0
    with sync.connect(b) as c:
        assert sync.rows(c) == before
        assert list(c.execute('SELECT * FROM _sync_shadow')) == shadow
        assert c.execute('SELECT count(*) FROM _sync_receipts').fetchone()[0] == 0
        assert c.execute('SELECT count(*) FROM _sync_outbox').fetchone()[0] == 0
        assert c.execute('SELECT emission_enabled FROM _sync_protocol').fetchone()[0] == 0
        assert c.execute('SELECT message FROM _sync_diagnostics WHERE path=?',(str(path),)).fetchone()[0] == 'resolution_apply_not_enabled'
        row = c.execute('SELECT authorization_state FROM _sync_packet_evidence WHERE uuid=?',(p['uuid'],)).fetchone()
        assert row[0] == 'verified'
    assert sync.receive(b, ex, security=sb if secure else None)['pending'] == 1


def test_old_reader_rejects_new_formats_without_receipt(versions, secure_peers):
    _, b, _, sa, _ = secure_peers
    with sync.connect(b) as c:
        with pytest.raises(ValueError): sync._validate(c, new_packet())
    # Exact retained v2 contract remains explicit, not widened to include v3 fields.
    assert set(new_packet(sa)) != signed.FIELDS
    assert signed.FORMAT == 'agentmesh-signed-changes-v2'
    with pytest.raises(ValueError, match='emission disabled'): sa.sign(new_packet())


@pytest.mark.parametrize('field', ['format','encoding','group','node','sender','uuid','kind','body','covers','evidence','checksum','key_id','signature'])
def test_v3_tampering_every_field(versions, secure_peers, field):
    _, b, _, sa, sb = secure_peers
    p = new_packet(sa)
    with sync.connect(b) as c, c:
        c.execute('BEGIN IMMEDIATE'); decode(versions, c, p, sb)
    damaged = copy.deepcopy(p)
    if field == 'body': damaged[field][0]['row']['last_event_id'] = 8
    elif field in ('covers','evidence'): damaged[field] = [{'uuid':str(uuid.uuid4()), 'envelope_digest':'b'*64}]
    elif field in ('checksum','key_id'): damaged[field] = 'f'*64
    elif field == 'signature': damaged[field] = 'f'*128
    elif field in ('uuid','sender','group'): damaged[field] = str(uuid.uuid4())
    elif field == 'node': damaged[field] = 'linux'
    elif field == 'kind': damaged[field] = 'resolution'
    else: damaged[field] += '-changed'
    with sync.connect(b) as c, c:
        c.execute('BEGIN IMMEDIATE')
        with pytest.raises(ValueError): decode(versions, c, damaged, sb)
    with sync.connect(b) as c:
        assert c.execute('SELECT count(*) FROM _sync_receipts').fetchone()[0] == 0
        assert c.execute('SELECT envelope_json FROM _sync_packet_evidence').fetchone()[0] == sync.canonical(p)


def test_revoked_uncommitted_evidence_refused(versions, store, secure_peers):
    _, b, ex, sa, sb = secure_peers
    p = new_packet(sa); publish_fixture(ex, p)
    assert sync.receive(b, ex, security=sb)['pending'] == 1
    signed.revoke(sb.directory, sa.public['key_id'])
    assert sync.receive(b, ex, security=sb)['invalid'] == 1
    with sync.connect(b) as c, c:
        c.execute('BEGIN IMMEDIATE')
        ref = contract.Ref(p['uuid'], versions.envelope_identity(p))
        # Loading retained bytes is deliberately not an authorization shortcut.
        stored = store.load_evidence(c, [ref])[p['uuid']]
        with pytest.raises(ValueError, match='revoked'): decode(versions, c, stored, sb)
        assert c.execute('SELECT count(*) FROM _sync_receipts').fetchone()[0] == 0


@pytest.mark.parametrize('disposition', [False, True])
@pytest.mark.parametrize('new', [False, True])
def test_historical_receipt_and_disposition_exact_replay(versions, store, secure_peers, disposition, new):
    _, b, ex, sa, sb = secure_peers
    if new:
        p = new_packet(sa); publish_fixture(ex, p)
    else:
        _, p = legacy_signed_packet(secure_peers)
    with sync.connect(b) as c, c:
        c.execute('BEGIN IMMEDIATE'); sb.guard(c); store.migrate(c)
        if disposition:
            # Future gate 3 writes this atomically with a real resolution commit.
            c.execute('INSERT INTO _sync_packet_dispositions VALUES(?,?,?,?)',
                      (p['uuid'], versions.envelope_identity(p), str(uuid.uuid4()), 'superseded'))
        else:
            c.execute('INSERT INTO _sync_receipts VALUES(?,?)',(p['uuid'],versions.receipt_identity(p)))
            # Simulate old receipts without complete envelope pins; real historical verification required.
    signed.revoke(sb.directory, sa.public['key_id'])
    with sync.connect(b) as c, c:
        c.execute('BEGIN IMMEDIATE')
        try: decode(versions, c, p, sb)
        except contract.MissingEvidence: pass  # Old baseline witness genuinely unavailable.
        if not disposition:
            assert c.execute('SELECT digest FROM _sync_receipt_envelopes WHERE uuid=?',(p['uuid'],)).fetchone()[0] == versions.envelope_identity(p)
    damaged = dict(p, signature='0'*128)
    with sync.connect(b) as c, c:
        c.execute('BEGIN IMMEDIATE')
        with pytest.raises(ValueError): decode(versions, c, damaged, sb)


def test_downgrade_and_limits_fail_closed(versions, secure_peers):
    _, b, _, sa, sb = secure_peers
    p = new_packet(sa)
    with sync.connect(b) as c, c:
        c.execute('BEGIN IMMEDIATE'); sb.guard(c)
        with pytest.raises(ValueError): decode(versions, c, new_packet(), sb)
        with pytest.raises(ValueError): decode(versions, c, p)
        with pytest.raises(ValueError): versions.decode_packet(c,p,security=sb,folder='linux',filename=p['uuid']+'.json')
        with pytest.raises(ValueError): versions.decode_packet(c,p,security=sb,folder='mac',filename='wrong.json')
    huge = new_packet(); huge['body'][0]['row']['updated_at'] = 'x'*(signed.MAX_BYTES+1)
    with pytest.raises(ValueError): versions.validate_wire(huge,group=GROUP,folder='mac')
    with pytest.raises(ValueError): versions.validate_wire(dict(new_packet(),format='future'),group=GROUP,folder='mac')


def test_v3_signature_domain_and_schema_are_enforced(versions, secure_peers):
    _, b, _, sa, sb = secure_peers
    p = new_packet(sa); unsigned = {k:v for k,v in p.items() if k != 'signature'}
    p['signature'] = sa.key.sign(signed.DOMAIN + signed.typed(unsigned)).hex()
    with sync.connect(b) as c, c:
        c.execute('BEGIN IMMEDIATE')
        with pytest.raises(ValueError): decode(versions,c,p,sb)
    p = new_packet(); del p['body'][0]['row']['updated_at']
    semantic = {k:p[k] for k in ('group','node','uuid','kind','body','covers','evidence')}
    p['checksum'] = contract.payload_digest(semantic)
    with sync.connect(b) as c, c:
        c.execute('BEGIN IMMEDIATE')
        with pytest.raises(ValueError, match='columns'): decode(versions,c,p)


def test_new_packet_publish_is_disabled_and_outbox_not_rewritten(versions, secure_peers):
    a, _, ex, _, _ = secure_peers
    p = new_packet()
    with sync.connect(a) as c, c:
        c.execute('INSERT INTO _sync_outbox(uuid,packet) VALUES(?,?)',(p['uuid'],sync.canonical(p)))
    with pytest.raises(ValueError, match='emission disabled'): sync.publish(a, ex)
    with sync.connect(a) as c:
        assert c.execute('SELECT packet,published FROM _sync_outbox').fetchone()[:] == (sync.canonical(p),0)
    assert not list(ex.rglob('*.json'))


def test_direct_v3_verify_and_digests_bind_complete_envelope(versions, secure_peers):
    _, _, _, sa, sb = secure_peers
    p = new_packet(sa)
    assert sb.verify(p, GROUP, 'mac') == p
    assert signed.receipt_digest(p) == hashlib.sha256(V3_DOMAIN + signed.typed({k:v for k,v in p.items() if k != 'signature'})).hexdigest()
    assert signed.envelope_digest(p) == hashlib.sha256(V3_DOMAIN + signed.typed(p)).hexdigest()
    assert versions.receipt_identity(new_packet()) != p['checksum']


def test_legacy_signing_call_contract_not_narrowed(versions, secure_peers):
    _, _, _, sa, _ = secure_peers
    _, old = legacy_signed_packet(secure_peers)
    # sign() historically filled format/checksum itself; preserve this valid v2 call.
    minimal = {k:old[k] for k in ('group','node','uuid','body')}
    assert sa.sign(minimal) == old


def test_recomputed_checksum_cannot_reauthorize_changed_coverage(versions, secure_peers):
    _, b, _, sa, sb = secure_peers
    p = new_packet(sa, kind='resolution')
    p['covers'][0]['envelope_digest'] = 'c'*64
    p['evidence'][0]['envelope_digest'] = 'c'*64
    p['checksum'] = contract.payload_digest({k:p[k] for k in ('group','node','uuid','kind','body','covers','evidence')})
    with sync.connect(b) as c, c:
        c.execute('BEGIN IMMEDIATE')
        with pytest.raises(ValueError, match='invalid packet signature'): decode(versions,c,p,sb)
        assert c.execute('SELECT count(*) FROM _sync_receipts').fetchone()[0] == 0
        # Positive control: the actual authorized sender can sign this revised proposal.
        p['signature'] = sa.key.sign(V3_DOMAIN + signed.typed({k:v for k,v in p.items() if k != 'signature'})).hex()
        assert decode(versions,c,p,sb).ref.uuid == p['uuid']


@pytest.mark.parametrize('committed', ['receipt', 'superseded'])
def test_committed_new_replay_does_not_count_signature_verifications(versions, store, secure_peers, committed):
    _, b, ex, sa, sb = secure_peers
    p = new_packet(sa); publish_fixture(ex, p)
    with sync.connect(b) as c, c:
        c.execute('BEGIN IMMEDIATE'); sb.guard(c); store.migrate(c)
        if committed == 'receipt':
            c.execute('INSERT INTO _sync_receipts VALUES(?,?)',(p['uuid'],versions.receipt_identity(p)))
            c.execute('INSERT INTO _sync_receipt_envelopes VALUES(?,?)',(p['uuid'],versions.envelope_identity(p)))
        else:
            c.execute('INSERT INTO _sync_packet_dispositions VALUES(?,?,?,?)',
                      (p['uuid'], versions.envelope_identity(p), str(uuid.uuid4()), 'superseded'))
    signed.revoke(sb.directory,sa.public['key_id'])
    assert sync.receive(b,ex,security=sb)['invalid'] == 0
    with sync.connect(b) as c:
        attempts = c.execute('SELECT coalesce(sum(attempts),0) FROM _sync_verification').fetchone()[0]
        assert attempts == 0
