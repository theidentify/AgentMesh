"""Independent set-union oracle; no SQL/signing or production ancestry helpers.

The driver models delivery, ordinary packet atomicity and superseded disposition.
Only resolution decisions are delegated to the implementation under test.
"""
import dataclasses
import itertools
import json
import random

import pytest

from test_resolution_protocol import api, change, evidence, make, scenario, uid


def oracle_ancestors(packets, origins):
    """Least fixed point of ancestor sets, unlike production graph traversal."""
    nodes = {c.revision: c for p in packets for c in p.changes}
    result = {rev: {rev} for rev in nodes}
    for rev, ch in nodes.items():
        if not ch.parents: result[rev].add(None)
        result[rev].update(p for p in ch.parents if p == origins[ch.key])
    changed = True
    while changed:
        changed = False
        for rev, ch in nodes.items():
            updated = result[rev] | set().union(*(result.get(p, {p}) for p in ch.parents))
            if updated != result[rev]: result[rev] = updated; changed = True
    return result


def oracle_decision(packet, packets, origins, heads):
    nodes = {c.revision: c for p in packets for c in p.changes}
    ancestry = oracle_ancestors(packets, origins)
    outcomes = []
    for ch in packet.changes:
        head = heads[ch.key]
        if head in ancestry[ch.revision]:
            outcomes.append((ch.key, ch.revision, ch.row_json))
        elif head in ancestry and ch.revision in ancestry[head]:
            outcomes.append((ch.key, head, nodes[head].row_json))
        else:
            return None
    return outcomes


def compare(api, target, packets, origins, heads):
    valid = api.validate_resolution(target, evidence(*packets), origins)
    expected = oracle_decision(target, [*packets, target], origins, heads)
    before = heads.copy()
    if expected is None:
        with pytest.raises(api.ResolutionConflict): api.plan_resolution(valid, heads)
        assert heads == before
        return None
    actual = api.plan_resolution(valid, heads)
    assert [(t.key, t.revision, t.row_json) for t in actual] == expected
    assert heads == before
    return actual


def drive(api, delivery, origins, initial=()):
    heads = dict(origins); rows = {}; received = {}; done = set(); superseded = set()
    for packet in initial:
        received[packet.ref.uuid] = packet; done.add(packet.ref.uuid)
        for ch in packet.changes: heads[ch.key] = ch.revision; rows[ch.key] = ch.row_json
    for packet in delivery:
        received[packet.ref.uuid] = packet
        progress = True
        while progress:
            progress = False
            for candidate in list(received.values()):
                ident = candidate.ref.uuid
                if ident in done or ident in superseded: continue
                if candidate.kind == 'change':
                    if not all(heads[c.key] == (c.parents[0] if c.parents else None) for c in candidate.changes): continue
                    for c in candidate.changes: heads[c.key] = c.revision; rows[c.key] = c.row_json
                else:
                    try: transitions = compare(api, candidate, list(received.values()), origins, heads)
                    except api.MissingEvidence: continue
                    if transitions is None: continue
                    for t in transitions: heads[t.key] = t.revision; rows[t.key] = t.row_json
                    superseded.update(ref.uuid for ref in candidate.covers)
                done.add(ident); progress = True
    return heads, rows, done, superseded


def test_three_peer_all_delivery_orders(api):
    p, q, r, origins = scenario(api)
    # Independent direct-parent acceptance is false at C's baseline: negative control.
    assert origins[r.changes[0].key] not in r.changes[0].parents
    for order in itertools.permutations([p, q, r]):
        for initial in [(p,), (q,), ()]:
            heads, rows, done, suppressed = drive(api, [*order, *reversed(order)], origins, initial)
            assert heads == {c.key: c.revision for c in r.changes}
            assert rows == {c.key: c.row_json for c in r.changes}
            assert r.ref.uuid in done
            assert suppressed == {p.ref.uuid, q.ref.uuid}


def terminal_scenario(api, same=False):
    p, q, r, origins = scenario(api)
    # S additionally absorbs w, absent from R, exposing incomplete T coverage bugs.
    w = make(api, 40, [change('w', 401, value=8)])
    origins[w.changes[0].key] = None
    s = make(api, 50, [change('x', 501, [uid(101), uid(201)], 5 if same else 9, origins[r.changes[0].key]),
                       change('y', 502, [uid(102)], 2), change('z', 503, [uid(202)], 4),
                       change('w', 504, [uid(401)], 8)], kind='resolution', covers=[p, q, w])
    t = make(api, 60, [change('x', 601, [uid(301), uid(501)], 10, origins[r.changes[0].key]),
                       change('y', 602, [uid(302), uid(502)], 2),
                       change('z', 603, [uid(303), uid(503)], None),
                       change('w', 604, [uid(504)], 8)], kind='resolution', covers=[p, q, r, w, s])
    return p, q, r, w, s, t, origins


@pytest.mark.parametrize('same', [False, True])
def test_independent_resolutions_need_terminal_choice(api, same):
    p, q, r, w, s, t, origins = terminal_scenario(api, same)
    heads = dict(origins); heads.update({c.key: c.revision for c in r.changes})
    assert compare(api, s, [p, q, r, w], origins, heads) is None
    assert compare(api, t, [p, q, r, w, s], origins, heads) is not None
    incomplete = dataclasses.replace(t, covers=tuple(ref for ref in t.covers if ref.uuid != p.ref.uuid))
    with pytest.raises(api.InvalidResolution): api.validate_resolution(incomplete, evidence(p, q, r, w, s), origins)
    # Exhaust all six packet deliveries, including T before either resolution.
    for order in itertools.permutations([p, q, r, w, s, t]):
        heads, rows, _, _ = drive(api, [*order, p, q, r, s], origins)
        assert heads == {c.key: c.revision for c in t.changes}
        assert rows == {c.key: c.row_json for c in t.changes}


def test_offline_edit_and_later_edit(api):
    p, q, r, origins = scenario(api)
    x = r.changes[0].key
    d = make(api, 40, [change('x', 401, [origins[x]], 7, origins[x])])
    heads = dict(origins); heads[x] = uid(401)
    assert compare(api, r, [p, q, d], origins, heads) is None
    u = make(api, 50, [change('x', 501, [uid(301)], 8, origins[x])])
    for order in itertools.permutations([p, q, r, u]):
        heads, rows, _, _ = drive(api, [*order, r, p, q], origins)
        assert heads[x] == uid(501)
        assert json.loads(rows[x])['last_event_id'] == 8
    heads = {c.key: c.revision for c in r.changes}; heads[x] = uid(501)
    assert compare(api, r, [p, q, u], origins, heads)[0].revision == uid(501)


@pytest.mark.parametrize('seed', range(40))
def test_seeded_graphs_match_independent_oracle(api, seed):
    rng = random.Random(seed)
    packets = []; origins = {}; frontier = {}; next_id = 1000
    keys = [api.Key('summary_state', (f'k{i}',)) for i in range(rng.randint(2, 6))]
    for k in keys: origins[k] = None; frontier[k] = []
    # Varied multi-key packet DAG, with later branches from randomly chosen ancestors.
    for i in range(rng.randint(3, 9)):
        changes = []
        for k in rng.sample(keys, rng.randint(1, len(keys))):
            prev = rng.choice(frontier[k]) if frontier[k] and rng.random() < .8 else None
            changes.append(change(k.values[0], next_id, [prev] if prev else [],
                                  None if rng.random() < .2 else rng.randint(0, 50)))
            frontier[k].append(uid(next_id)); next_id += 1
        packets.append(make(api, 100+i, changes))
    active = {c.key for p in packets for c in p.changes}
    ancestry = oracle_ancestors(packets, origins)
    final_changes = []
    for k in sorted(active):
        maxima = [v for v in frontier[k] if not any(v != other and v in ancestry[other] for other in frontier[k])]
        final_changes.append(change(k.values[0], next_id, maxima, rng.randint(0, 100)))
        next_id += 1
    r = make(api, 900, final_changes, kind='resolution', covers=packets)
    for _ in range(8):
        heads = {k: rng.choice([None, *frontier[k]]) for k in active}
        rng.shuffle(packets)
        result = compare(api, r, packets, origins, heads)
        assert result is not None, f'seed={seed}'
        assert {t.key: t.revision for t in result} == {c.key: c.revision for c in r.changes}, f'seed={seed}'
    delivery = [*packets, r, *rng.sample(packets, len(packets))]; rng.shuffle(delivery)
    heads, rows, _, _ = drive(api, delivery, origins)
    assert all(heads[c.key] == c.revision and rows[c.key] == c.row_json for c in r.changes), f'seed={seed}'
