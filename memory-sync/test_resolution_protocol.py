"""Gate-1 pure contract tests. No live sync integration or new emission."""
import copy
import dataclasses
import hashlib
import importlib
import itertools
import json
from pathlib import Path
import uuid

import pytest


def uid(n):
    return str(uuid.UUID(int=n))


def key(name):
    return ('summary_state', (name,))


def row(name, value):
    return None if value is None else {'consumer': name, 'last_event_id': value, 'updated_at': 'fixture'}


def change(name, rev, parents=(), value=1, origin=None):
    return {'table': 'summary_state', 'key': [name], 'row': row(name, value),
            'revision': uid(rev), 'parents': sorted(parents), 'origin': origin}


def payload(n, changes, *, kind='change', covers=(), evidence=None):
    refs = lambda ps: [{'uuid': p.ref.uuid, 'envelope_digest': p.ref.digest}
                       for p in sorted(ps, key=lambda p: p.ref.uuid)]
    return {'group': uid(1), 'node': 'mac', 'uuid': uid(n), 'kind': kind,
            'body': sorted(changes, key=lambda c: (c['table'], json.dumps(c['key'], separators=(',', ':')))),
            'covers': refs(covers), 'evidence': refs(covers if evidence is None else evidence)}


def make(api, n, changes, **kwargs):
    p = payload(n, changes, **kwargs)
    # Fixture envelope identity; authentication belongs to the future adapter.
    return api.packet_from_payload(p, envelope_digest=hashlib.sha256(json.dumps(p, sort_keys=True).encode()).hexdigest())


@pytest.fixture
def api():
    # Import in fixtures so red tests report missing functionality, not just one collection error.
    return importlib.import_module('resolution_protocol')


def scenario(api):
    base = 'baseline:' + 'b' * 64
    p = make(api, 10, [change('x', 101, [base], 1, base), change('y', 102, value=2)])
    q = make(api, 20, [change('x', 201, [base], 3, base), change('z', 202, value=4)])
    r = make(api, 30, [change('x', 301, [uid(101), uid(201)], 5, base),
                       change('y', 302, [uid(102)], 2), change('z', 303, [uid(202)], 4)],
             kind='resolution', covers=[p, q])
    origins = {api.Key(*key('x')): base, api.Key(*key('y')): None, api.Key(*key('z')): None}
    return p, q, r, origins


def evidence(*packets):
    return {p.ref.uuid: p for p in packets}


def altered(api, packet, **fields):
    return dataclasses.replace(packet, **fields)


def test_payload_digest_binds_every_semantic_field(api):
    p, q, _, _ = scenario(api)
    raw = payload(30, [change('x', 301, [uid(101), uid(201)])], kind='resolution', covers=[p, q])
    digest = api.payload_digest(raw)
    assert api.payload_digest(dict(reversed(list(raw.items())))) == digest
    edits = {'group': uid(2), 'node': 'linux', 'uuid': uid(31), 'kind': 'change',
             'body': [change('x', 302)], 'covers': [], 'evidence': []}
    for field, value in edits.items():
        assert api.payload_digest(dict(raw, **{field: value})) != digest, field
    for field, value in {'parents': [uid(999)], 'origin': 'baseline:' + 'a'*64,
                         'revision': uid(999), 'row': row('x', 999), 'key': ['other'],
                         'table': 'ingestion_cursors'}.items():
        modified = copy.deepcopy(raw); modified['body'][0][field] = value
        assert api.payload_digest(modified) != digest, field


@pytest.mark.parametrize('damage', [
    'extra', 'uuid', 'node', 'empty', 'kind', 'scalar-parent', 'duplicate-parent',
    'unsorted-parent', 'bad-origin', 'wrong-key', 'unknown-table', 'key-bool',
    'row-not-object', 'change-extra', 'duplicate-key', 'unsorted-body', 'duplicate-revision',
    'normal-covers', 'bad-digest', 'duplicate-ref', 'unsorted-ref', 'missing-evidence',
])
def test_payload_rejects_malformed_contract(api, damage):
    p, q, _, _ = scenario(api)
    raw = payload(30, [change('x', 301, [uid(101), uid(201)]), change('y', 302, [uid(102)])],
                  kind='resolution', covers=[p, q])
    if damage == 'extra': raw['unexpected'] = True
    elif damage == 'uuid': raw['uuid'] = 'not-uuid'
    elif damage == 'node': raw['node'] = 'unknown'
    elif damage == 'empty': raw['body'] = []
    elif damage == 'kind': raw['kind'] = 'overwrite'
    elif damage == 'scalar-parent': raw['body'][0]['parents'] = uid(101)
    elif damage == 'duplicate-parent': raw['body'][0]['parents'] = [uid(101)] * 2
    elif damage == 'unsorted-parent': raw['body'][0]['parents'].reverse()
    elif damage == 'bad-origin': raw['body'][0]['origin'] = uid(101)
    elif damage == 'wrong-key': raw['body'][0]['row']['consumer'] = 'other'
    elif damage == 'unknown-table': raw['body'][0]['table'] = 'secrets'
    elif damage == 'key-bool': raw['body'][0]['key'] = [True]
    elif damage == 'row-not-object': raw['body'][0]['row'] = 'content'
    elif damage == 'change-extra': raw['body'][0]['parent'] = None
    elif damage == 'duplicate-key': raw['body'][1]['key'] = ['x']; raw['body'][1]['row'] = row('x', 9)
    elif damage == 'unsorted-body': raw['body'].reverse()
    elif damage == 'duplicate-revision': raw['body'][1]['revision'] = raw['body'][0]['revision']
    elif damage == 'normal-covers': raw['kind'] = 'change'
    elif damage == 'bad-digest': raw['covers'][0]['envelope_digest'] = 'x'
    elif damage == 'duplicate-ref': raw['covers'].append(raw['covers'][0])
    elif damage == 'unsorted-ref': raw['covers'].reverse()
    elif damage == 'missing-evidence': raw['evidence'] = []
    with pytest.raises(api.InvalidResolution):
        api.packet_from_payload(raw, envelope_digest='d'*64)


def test_valid_resolution_covers_companions_from_both_packets(api):
    p, q, r, origins = scenario(api)
    validated = api.validate_resolution(r, evidence(p, q), origins)
    result = api.plan_resolution(validated, origins)
    assert {t.key: t.revision for t in result} == {c.key: c.revision for c in r.changes}
    assert all(t.action == 'install' for t in result)
    assert {t.key.values[0]: json.loads(t.row_json)['last_event_id'] for t in result} == {'x': 5, 'y': 2, 'z': 4}
    tombstone = altered(api, r, changes=(dataclasses.replace(r.changes[0], row_json=None), *r.changes[1:]))
    assert api.plan_resolution(api.validate_resolution(tombstone, evidence(p, q), origins), origins)[0].row_json is None


@pytest.mark.parametrize('damage', ['companion', 'parent-packet', 'frontier', 'redundant-frontier', 'extra-key'])
def test_incomplete_coverage_is_refused(api, damage):
    p, q, r, origins = scenario(api)
    if damage == 'companion': r = altered(api, r, changes=r.changes[:1])
    elif damage == 'parent-packet': r = altered(api, r, covers=(p.ref,))
    elif damage == 'frontier': r = altered(api, r, changes=(dataclasses.replace(r.changes[0], parents=(uid(101),)), *r.changes[1:]))
    elif damage == 'redundant-frontier': r = altered(api, r, changes=(dataclasses.replace(r.changes[0], parents=tuple(sorted((*r.changes[0].parents, origins[r.changes[0].key])))), *r.changes[1:]))
    else:
        extra = make(api, 99, [change('extra', 999)]).changes[0]
        origins[extra.key] = None
        r = altered(api, r, changes=tuple(sorted((*r.changes, extra), key=lambda c: c.key)))
    with pytest.raises(api.InvalidResolution): api.validate_resolution(r, evidence(p, q), origins)


def test_missing_or_changed_evidence(api):
    p, q, r, origins = scenario(api)
    with pytest.raises(api.MissingEvidence): api.validate_resolution(r, evidence(p), origins)
    bad = altered(api, q, ref=api.Ref(q.ref.uuid, 'f'*64))
    with pytest.raises(api.InvalidResolution): api.validate_resolution(r, evidence(p, bad), origins)
    with pytest.raises(api.MissingEvidence): api.validate_resolution(r, evidence(p, q), {})
    # A retained packet may not redefine an existing revision, even with equal row bytes.
    duplicate = altered(api, p, ref=api.Ref(uid(90), 'e'*64))
    with pytest.raises(api.InvalidResolution): api.validate_resolution(r, evidence(p, q, duplicate), origins)


@pytest.mark.parametrize('damage', ['cycle', 'cross-key', 'origin', 'unknown-parent', 'ordinary-merge', 'self-cover'])
def test_graph_refuses_cycles_cross_key_parents_and_origin_mismatch(api, damage):
    p, q, r, origins = scenario(api)
    first = p.changes[0]
    if damage == 'cycle': first = dataclasses.replace(first, parents=(r.changes[0].revision,))
    elif damage == 'cross-key': first = dataclasses.replace(first, parents=(p.changes[1].revision,))
    elif damage == 'origin': origins[first.key] = 'baseline:' + 'a'*64
    elif damage == 'unknown-parent': first = dataclasses.replace(first, parents=(uid(998),))
    elif damage == 'ordinary-merge': first = dataclasses.replace(first, parents=(uid(201), origins[first.key]))
    elif damage == 'self-cover': r = altered(api, r, covers=(*r.covers, r.ref), evidence=(*r.evidence, r.ref))
    p = altered(api, p, changes=(first, *p.changes[1:]))
    error = api.MissingEvidence if damage == 'unknown-parent' else api.InvalidResolution
    with pytest.raises(error): api.validate_resolution(r, evidence(p, q), origins)


@pytest.mark.parametrize('bound,value', [('packets', 1), ('revisions', 6), ('keys', 2), ('parents', 1)])
def test_limits_fail_closed(api, bound, value):
    p, q, r, origins = scenario(api)
    api.validate_resolution(r, evidence(p, q), origins)  # positive control
    with pytest.raises(api.InvalidResolution):
        api.validate_resolution(r, evidence(p, q), origins, limits=dataclasses.replace(api.Limits(), **{bound: value}))


def test_transition_is_atomic_and_preserves_descendants(api):
    p, q, r, origins = scenario(api)
    u = make(api, 40, [change('x', 401, [uid(301)], 6, origins[r.changes[0].key])])
    sibling = make(api, 50, [change('z', 501, [uid(202)], 7)])
    v = api.validate_resolution(r, evidence(p, q, u, sibling), origins)
    heads = dict(origins)
    for c in p.changes: heads[c.key] = c.revision
    assert all(t.action == 'install' for t in api.plan_resolution(v, heads))
    heads.update({r.changes[0].key: uid(401), r.changes[1].key: uid(302)})
    result = api.plan_resolution(v, heads)
    assert result[0].action == 'preserve' and result[0].revision == uid(401)
    assert json.loads(result[0].row_json)['last_event_id'] == 6
    assert result[1].action == 'preserve'
    heads[r.changes[2].key] = uid(501)
    before = heads.copy()
    with pytest.raises(api.ResolutionConflict): api.plan_resolution(v, heads)
    assert heads == before
    heads[r.changes[2].key] = uid(999)
    with pytest.raises(api.MissingEvidence): api.plan_resolution(v, heads)
    heads.pop(r.changes[2].key)
    with pytest.raises(api.MissingEvidence): api.plan_resolution(v, heads)


def test_existing_runtime_remains_unchanged(api, tmp_path):
    import memory_sync
    import signed_packets
    import sqlite_memory
    assert memory_sync.FORMAT == 'omp-memory-changes-v1'
    assert signed_packets.FORMAT == 'agentmesh-signed-changes-v2'
    assert signed_packets.DOMAIN == b'AgentMesh/change-packet/Ed25519/v2\x00'
    db = tmp_path/'memory.db'; sqlite_memory.init_database(db)
    memory_sync.initialize(db, 'windows', uid(1))
    packet = dict(payload(10, [change('x', 101)]), format=api.UNSIGNED_FORMAT, checksum='a'*64)
    with memory_sync.connect(db) as c:
        with pytest.raises(ValueError): memory_sync._validate(c, packet)
        assert c.execute('SELECT count(*) FROM _sync_receipts').fetchone()[0] == 0
        assert not c.execute("SELECT name FROM sqlite_master WHERE name='_sync_protocol'").fetchall()
    signed = dict(packet, format=api.SIGNED_FORMAT, encoding=signed_packets.ENCODING,
                  sender=uid(2), key_id='a'*64, signature='a'*128)
    with pytest.raises(ValueError): signed_packets.validate_envelope(signed, uid(1), 'mac')
    for source in Path(__file__).parent.glob('*.py'):
        if source.name == 'resolution_protocol.py' or source.name.startswith('test_'): continue
        assert 'import resolution_protocol' not in source.read_text(), source
        assert 'from resolution_protocol' not in source.read_text(), source


def test_missing_current_baseline_is_refused_not_pending(api):
    p, q, r, origins = scenario(api)
    validated = api.validate_resolution(r, evidence(p, q), origins)
    heads = dict(origins); heads[r.changes[0].key] = None
    with pytest.raises(api.InvalidResolution): api.plan_resolution(validated, heads)


def test_ancestry_budget_and_deep_chain(api):
    origins = {api.Key(*key('x')): None}
    packets = []; parent = []
    for i in range(150):
        p = make(api, 100+i, [change('x', 1000+i, parent)])
        packets.append(p); parent = [uid(1000+i)]
    r = make(api, 999, [change('x', 9999, parent)], kind='resolution', covers=packets)
    valid = api.validate_resolution(r, evidence(*packets), origins)
    assert len(valid.ancestors[uid(9999)]) == 152
    assert api.plan_resolution(valid, origins)[0].revision == uid(9999)
    with pytest.raises(api.InvalidResolution):
        api.validate_resolution(r, evidence(*packets), origins, limits=api.Limits(ancestry=100))
    with pytest.raises(api.InvalidResolution):
        api.validate_resolution(r, evidence(*packets), origins, limits=api.Limits(keys=0))


def test_scope_and_normalized_records_cannot_bypass_validation(api):
    p, q, r, origins = scenario(api)
    for bad in [dataclasses.replace(q, group=uid(9)),
                dataclasses.replace(q, changes=list(q.changes)),
                dataclasses.replace(q, changes=(dataclasses.replace(q.changes[0], row_json='not json'), *q.changes[1:]))]:
        with pytest.raises(api.InvalidResolution): api.validate_resolution(r, evidence(p, bad), origins)
    with pytest.raises(api.InvalidResolution): api.validate_resolution(r, {uid(99): p, q.ref.uuid: q}, origins)
    # Snapshot is immutable and isolated from future changes in caller mappings.
    valid = api.validate_resolution(r, evidence(p, q), origins)
    with pytest.raises(TypeError): valid.ancestors[uid(301)] = frozenset()
    origins.clear()
    assert len(valid.origins) == 3


def test_predecessor_packets_must_be_covered_wholly(api):
    base = make(api, 5, [change('x', 51), change('w', 52)])
    p = make(api, 10, [change('x', 101, [uid(51)])])
    r = make(api, 30, [change('x', 301, [uid(101)])], kind='resolution', covers=[p], evidence=[base, p])
    origins = {api.Key(*key(k)): None for k in ('x', 'w')}
    with pytest.raises(api.InvalidResolution): api.validate_resolution(r, evidence(base, p), origins)
    good = make(api, 30, [change('x', 301, [uid(101)]), change('w', 302, [uid(52)])],
                kind='resolution', covers=[base, p])
    api.validate_resolution(good, evidence(base, p), origins)


def test_key_contract_matches_legacy_and_allocation_owner(api):
    import memory_sync
    assert api.KEY_COLUMNS == memory_sync.KEYS
    raw = payload(10, [{'table': 'memory_items', 'key': [2**40], 'revision': uid(101),
                       'parents': [], 'origin': None, 'row': {'id': 2**40}}])
    with pytest.raises(api.InvalidResolution): api.packet_from_payload(raw, envelope_digest='a'*64)
    raw['node'] = 'windows'
    api.packet_from_payload(raw, envelope_digest='a'*64)
    # A Mac editor may edit a proved Windows-created ID; creation range is not reapplied.
    raw['node'] = 'mac'; raw['body'][0]['parents'] = [uid(100)]
    api.packet_from_payload(raw, envelope_digest='a'*64)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), object()])
def test_unsupported_row_values_are_refused(api, value):
    raw = payload(10, [change('x', 101)])
    raw['body'][0]['row']['metadata'] = value
    with pytest.raises(api.InvalidResolution): api.packet_from_payload(raw, envelope_digest='a'*64)
