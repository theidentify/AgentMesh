"""Pure resolution contract: no I/O, signatures, SQL, or emission.

Inputs to validate_resolution must already have authenticated envelope bindings
and verified origins. This module proves semantic coverage, not authorization.
Gate-2 wire readers supply bindings; transactional application remains gated off.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
import json
from types import MappingProxyType
from typing import Mapping
import uuid

from signed_packets import typed

UNSIGNED_FORMAT = 'omp-memory-changes-v2'
SIGNED_FORMAT = 'agentmesh-signed-changes-v3'
SEMANTIC_DOMAIN = b'AgentMesh/resolution-payload/v1\x00'
# Deliberately no changes to memory_sync.FORMAT or signed_packets.FORMAT.
KEY_COLUMNS = {name: ('id',) for name in (
    'ingestion_errors', 'observation_events', 'memory_summaries', 'memory_items', 'source_sessions')}
KEY_COLUMNS.update(ingestion_cursors=('source_path',), memory_sources=('memory_id', 'event_id'),
                   summary_state=('consumer',))
SLOTS = {'windows': (2**40, 2**41), 'mac': (2**41, 3*2**40), 'linux': (3*2**40, 2**42)}


class InvalidResolution(ValueError):
    """Malformed definition, identity, graph, or declared coverage."""


class MissingEvidence(ValueError):
    """Required proof is unavailable; a future adapter may retry."""


class ResolutionConflict(ValueError):
    """A known current branch is incomparable with the proposed resolution."""


@dataclass(frozen=True, order=True)
class Key:
    table: str
    values: tuple[int | str, ...]


@dataclass(frozen=True, order=True)
class Ref:
    uuid: str
    digest: str


@dataclass(frozen=True)
class Change:
    key: Key
    revision: str
    parents: tuple[str, ...]
    origin: str | None
    row_json: str | None


@dataclass(frozen=True)
class Packet:
    ref: Ref
    kind: str
    changes: tuple[Change, ...]
    covers: tuple[Ref, ...]
    evidence: tuple[Ref, ...]
    group: str
    node: str


@dataclass(frozen=True)
class Limits:
    packets: int = 256
    revisions: int = 4096
    keys: int = 1024
    parents: int = 64
    ancestry: int = 262144


@dataclass(frozen=True)
class Resolution:
    packet: Packet
    ancestors: Mapping[str, frozenset[str | None]]
    changes: Mapping[str, Change]
    origins: Mapping[Key, str | None]


@dataclass(frozen=True)
class Transition:
    key: Key
    revision: str
    row_json: str | None
    action: str


def _require(condition, message):
    if not condition:
        raise InvalidResolution(message)


def _uuid(value):
    if type(value) is not str:
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _hex(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _baseline(value):
    return type(value) is str and value.startswith('baseline:') and _hex(value[9:])


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def payload_digest(payload: dict) -> str:
    """Hash all semantic fields. Shape validation is a separate operation."""
    try:
        return hashlib.sha256(SEMANTIC_DOMAIN + typed(payload)).hexdigest()
    except (ValueError, TypeError, RecursionError, OverflowError) as exc:
        raise InvalidResolution('unsupported typed payload') from exc


def _refs(values):
    _require(type(values) is list and len(values) <= Limits().packets, 'invalid references')
    result = []
    for ref in values:
        _require(type(ref) is dict and set(ref) == {'uuid', 'envelope_digest'}, 'invalid reference fields')
        _require(_uuid(ref['uuid']) and _hex(ref['envelope_digest']), 'invalid reference identity')
        result.append(Ref(ref['uuid'], ref['envelope_digest']))
    _require([r.uuid for r in result] == sorted({r.uuid for r in result}), 'references must be sorted and unique')
    return tuple(result)


def packet_from_payload(payload: dict, *, envelope_digest: str) -> Packet:
    """Normalize a bounded semantic payload; does NOT authenticate the envelope."""
    _require(type(payload) is dict and set(payload) == {'group', 'node', 'uuid', 'kind', 'body', 'covers', 'evidence'},
             'invalid payload fields')
    _require(_uuid(payload['group']) and _uuid(payload['uuid']) and _hex(envelope_digest), 'invalid packet identity')
    _require(type(payload['node']) is str and payload['node'] in SLOTS, 'invalid node')
    _require(payload['kind'] in ('change', 'resolution'), 'invalid kind')
    body = payload['body']
    _require(type(body) is list and 0 < len(body) <= Limits().keys, 'invalid body size')
    payload_digest(payload)  # enforce typed depth/value/number limits before normalizing rows
    covers, evidence = _refs(payload['covers']), _refs(payload['evidence'])
    _require(set(covers) <= set(evidence), 'covered packets require pinned evidence')
    _require(all(ref.uuid != payload['uuid'] for ref in (*covers, *evidence)), 'self reference')
    if payload['kind'] == 'change':
        _require(not covers and not evidence, 'ordinary packet cannot cover evidence')
    else:
        _require(bool(covers), 'resolution requires coverage')
    changes = []
    identities = []
    for raw in body:
        _require(type(raw) is dict and set(raw) == {'table', 'key', 'row', 'revision', 'parents', 'origin'},
                 'invalid change fields')
        table, values = raw['table'], raw['key']
        _require(type(table) is str and table in KEY_COLUMNS, 'invalid table')
        columns = KEY_COLUMNS[table]
        _require(type(values) is list and len(values) == len(columns), 'invalid key shape')
        for name, value in zip(columns, values):
            if name.endswith('id'):
                _require(type(value) is int and 0 < value < 2**42, 'invalid integer key')
            else:
                _require(type(value) is str and bool(value), 'invalid text key')
        _require(_uuid(raw['revision']), 'invalid revision')
        origin = raw['origin']
        _require(origin is None or _baseline(origin), 'invalid origin')
        parents = raw['parents']
        _require(type(parents) is list and len(parents) <= Limits().parents, 'invalid parents')
        _require(all(_uuid(p) or _baseline(p) for p in parents), 'invalid parent identity')
        _require(parents == sorted(set(parents)), 'parents must be sorted and unique')
        if payload['kind'] == 'change':
            _require(len(parents) == 1 or (not parents and origin is None), 'invalid ordinary ancestry')
            if not parents and columns == ('id',):
                low, high = SLOTS[payload['node']]
                _require(low <= values[0] < high or (payload['node'] == 'mac' and values[0] < 2**40),
                         'new ID outside sender allocation range')
        else:
            _require(bool(parents), 'resolution requires frontier')
        row = raw['row']
        _require(row is None or type(row) is dict, 'invalid row')
        if row is not None:
            _require(all(name in row and type(row[name]) is type(value) and row[name] == value
                         for name, value in zip(columns, values)), 'row key mismatch')
        k = Key(table, tuple(values))
        changes.append(Change(k, raw['revision'], tuple(parents), origin,
                              _canonical(row) if row is not None else None))
        identities.append((table, _canonical(values)))
    _require(identities == sorted(set(identities)), 'body keys must be sorted and unique')
    _require(len({c.revision for c in changes}) == len(changes), 'duplicate revision')
    return Packet(Ref(payload['uuid'], envelope_digest), payload['kind'], tuple(changes), covers, evidence,
                  payload['group'], payload['node'])


def _check_packet(packet):
    """Revalidate direct frozen-record construction too; no mutable row aliases."""
    try:
        raw = {'group': packet.group, 'node': packet.node, 'uuid': packet.ref.uuid, 'kind': packet.kind,
               'body': [{'table': c.key.table, 'key': list(c.key.values),
                         'row': json.loads(c.row_json) if c.row_json is not None else None,
                         'revision': c.revision, 'parents': list(c.parents), 'origin': c.origin} for c in packet.changes],
               'covers': [{'uuid': r.uuid, 'envelope_digest': r.digest} for r in packet.covers],
               'evidence': [{'uuid': r.uuid, 'envelope_digest': r.digest} for r in packet.evidence]}
        _require(packet_from_payload(raw, envelope_digest=packet.ref.digest) == packet, 'noncanonical packet record')
    except (AttributeError, TypeError, KeyError, RecursionError, json.JSONDecodeError) as exc:
        raise InvalidResolution('invalid packet record') from exc


def validate_resolution(packet: Packet, evidence: Mapping[str, Packet],
                        origins: Mapping[Key, str | None], *, limits: Limits = Limits()) -> Resolution:
    """Prove complete packet closure and maximal per-key parents, without applying.

    Evidence may include additional authenticated local descendants of packet.
    A declared missing reference is pending. An available but omitted companion
    or ancestor packet is invalid. Origins are verified by the future adapter.
    """
    _require(all(type(v) is int and v > 0 for v in vars(limits).values()), 'invalid limits')
    _check_packet(packet)
    _require(packet.kind == 'resolution', 'expected resolution')
    _require(len(evidence) <= limits.packets, 'packet limit')
    packets = dict(evidence)
    if packet.ref.uuid in packets:
        _require(packets[packet.ref.uuid] == packet, 'packet UUID reused')
    packets[packet.ref.uuid] = packet
    _require(len(packets) <= limits.packets, 'packet limit')
    nodes, owner = {}, {}
    for ident, p in packets.items():
        _check_packet(p)
        _require(ident == p.ref.uuid and p.group == packet.group, 'packet scope or identity mismatch')
        for ref in (*p.covers, *p.evidence):
            if ref.uuid not in packets:
                raise MissingEvidence('packet evidence unavailable')
            _require(packets[ref.uuid].ref == ref, 'evidence digest mismatch')
        for ch in p.changes:
            _require(ch.revision not in nodes, 'revision reused')
            _require(len(ch.parents) <= limits.parents, 'parent limit')
            nodes[ch.revision] = ch; owner[ch.revision] = ident
    _require(len(nodes) <= limits.revisions, 'revision limit')
    keys = {ch.key for ch in nodes.values()}
    _require(len(keys) <= limits.keys, 'key limit')
    for k in keys:
        if k not in origins:
            raise MissingEvidence('origin witness unavailable')
        _require(origins[k] is None or _baseline(origins[k]), 'invalid origin witness')

    # Kahn traversal detects cycles without recursion and constructs bounded proofs.
    children = {rev: [] for rev in nodes}
    degree = {}
    ancestry = {}
    for rev, ch in nodes.items():
        _require(ch.origin == origins[ch.key], 'baseline or origin mismatch')
        degree[rev] = 0
        ancestry[rev] = {rev}
        if not ch.parents:
            _require(ch.origin is None, 'missing baseline parent')
            ancestry[rev].add(None)
        for parent in ch.parents:
            if _baseline(parent):
                _require(parent == ch.origin, 'baseline parent mismatch')
                ancestry[rev].add(parent)
            else:
                if parent not in nodes:
                    raise MissingEvidence('parent evidence unavailable')
                _require(nodes[parent].key == ch.key, 'cross-key parent')
                children[parent].append(rev); degree[rev] += 1
    queue = deque(rev for rev, count in degree.items() if count == 0)
    visited = 0
    memberships = sum(map(len, ancestry.values()))
    _require(memberships <= limits.ancestry, 'ancestry limit')
    while queue:
        parent = queue.popleft(); visited += 1
        for child in children[parent]:
            extra = ancestry[parent] - ancestry[child]
            memberships += len(extra)
            _require(memberships <= limits.ancestry, 'ancestry limit')
            ancestry[child].update(extra)
            degree[child] -= 1
            if degree[child] == 0: queue.append(child)
    _require(visited == len(nodes), 'cyclic revision graph')

    for p in packets.values():
        if p.kind != 'resolution': continue
        covered = {r.uuid for r in p.covers}
        # Whole-packet closure includes prior resolutions' coverage and every
        # packet owning a covered revision's predecessor, not just selected keys.
        for ident in covered:
            predecessor = packets[ident]
            _require({r.uuid for r in predecessor.covers} <= covered, 'incomplete transitive coverage')
            for ch in predecessor.changes:
                for parent in ch.parents:
                    if parent in owner:
                        _require(owner[parent] in covered, 'missing predecessor packet coverage')
        covered_nodes = {ch.revision: ch for ident in covered for ch in packets[ident].changes}
        by_key = {}
        for rev, ch in covered_nodes.items(): by_key.setdefault(ch.key, set()).add(rev)
        _require(set(by_key) == {ch.key for ch in p.changes}, 'incomplete key coverage')
        for ch in p.changes:
            revisions = by_key[ch.key]
            nonmaximal = set()
            for rev in revisions: nonmaximal.update((ancestry[rev] - {rev}) & revisions)
            _require(set(ch.parents) == revisions - nonmaximal, 'incomplete or redundant frontier')
    return Resolution(packet, MappingProxyType({r: frozenset(a) for r, a in ancestry.items()}),
                      MappingProxyType(nodes), MappingProxyType({k: origins[k] for k in keys}))


def plan_resolution(resolution: Resolution, heads: Mapping[Key, str | None]) -> tuple[Transition, ...]:
    """All-or-nothing pure plan. Unknown/incomparable heads never return a prefix."""
    result = []
    for ch in resolution.packet.changes:
        if ch.key not in heads:
            raise MissingEvidence('current head unavailable')
        head = heads[ch.key]
        _require(head is None or _uuid(head) or _baseline(head), 'invalid current head')
        _require(head is not None or resolution.origins[ch.key] is None, 'current baseline missing')
        if head is not None and head in resolution.changes:
            _require(resolution.changes[head].key == ch.key, 'current head belongs to another key')
        if head in resolution.ancestors[ch.revision]:
            action = 'preserve' if head == ch.revision else 'install'
            result.append(Transition(ch.key, ch.revision, ch.row_json, action))
        elif head in resolution.ancestors and ch.revision in resolution.ancestors[head]:
            current = resolution.changes[head]
            result.append(Transition(ch.key, head, current.row_json, 'preserve'))
        elif head == resolution.origins[ch.key] or head in resolution.changes:
            raise ResolutionConflict('incomparable current head')
        elif _baseline(head):
            raise InvalidResolution('current baseline mismatch')
        else:
            raise MissingEvidence('current head evidence unavailable')
    return tuple(result)
