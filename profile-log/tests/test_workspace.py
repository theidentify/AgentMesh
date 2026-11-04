import json
import pytest
from agentmesh_log import workspace as ws
from agentmesh_log.identity import DeviceIdentity
from agentmesh_log.broker.keystore import BrokerState, BrokerHome, FileKeyStore
from agentmesh_log.storage.filesystem import FilesystemStorage

WS = 'ab' * 16


class FakeRegistry:
    """Devices of several accounts (real accounts other than the registry's own arrive in W2)."""
    def __init__(self): self.certificates, self.revoked = {}, {}
    def device(self, account):
        d = DeviceIdentity(); self.certificates[d.device_id] = dict(account_id=account, public=d.public_bytes); return d
    def device_public(self, device_id, at=None):
        if device_id not in self.certificates: raise ValueError('unknown')
        if device_id in self.revoked and at >= self.revoked[device_id]: raise ValueError('revoked')
        return self.certificates[device_id]['public']


O1, O2, AD, M = ('11' * 16), ('22' * 16), ('33' * 16), ('44' * 16)


def world():
    r = FakeRegistry()
    return r, {a: r.device(a) for a in (O1, O2, AD, M)}


def chain(r, *steps):
    entries, head, t = [], None, 1000
    for device, op, kw in steps:
        t += 10
        e = ws.create(device, WS, op, previous=head, issued_at=t, **kw)
        entries.append(e); head = ws.entry_hash(e)
    return entries


def test_genesis_roles_and_settings():
    r, d = world()
    es = chain(r, (d[O1], 'genesis', dict(subject=O1, role='owner')),
               (d[O1], 'member', dict(subject=AD, role='admin')),
               (d[AD], 'member', dict(subject=M, role='member')),
               (d[AD], 'settings', dict(settings={'task_lease.max_ms': 7_200_000})))
    w = ws.replay(WS, es, r, O1)
    assert w.state.members == {O1: 'owner', AD: 'admin', M: 'member'} and w.ignored == []
    assert w.lease(10**12) == (ws.DEFAULT_LEASE_MS, 7_200_000)
    assert w.lease(1015) == (ws.DEFAULT_LEASE_MS, ws.MAX_LEASE_MS)   # settings apply only from their time
    assert w.role(M, 1025) is None and w.role(M, 1030) == 'member'


def test_role_limits():
    r, d = world()
    base = chain(r, (d[O1], 'genesis', dict(subject=O1, role='owner')),
                 (d[O1], 'member', dict(subject=AD, role='admin')),
                 (d[O1], 'member', dict(subject=M, role='member')))
    head = ws.entry_hash(base[-1])
    for device, kw in ((d[AD], dict(subject=O2, role='admin')), (d[AD], dict(subject=O1, role=None)),
                       (d[M], dict(subject=O2, role='member')), (d[O1], dict(subject=O1, role=None))):
        bad = ws.create(device, WS, 'member', previous=head, issued_at=2000, **kw)
        w = ws.replay(WS, base + [bad], r, O1)
        assert ws.entry_hash(bad) in w.ignored and w.head == head, kw
    # a genesis by another account, or a forged signature, never counts
    other = ws.create(d[O2], WS, 'genesis', subject=O2, role='owner', issued_at=1)
    forged = dict(base[1], role='owner')
    w = ws.replay(WS, [other, base[0], forged], r, O1)
    assert w.state.members == {O1: 'owner'} and ws.entry_hash(other) in w.ignored and ws.entry_hash(forged) in w.ignored


def test_owner_changes_need_a_second_owner():
    r, d = world()
    base = chain(r, (d[O1], 'genesis', dict(subject=O1, role='owner')),
                 (d[O1], 'member', dict(subject=O2, role='owner')))   # one owner may add a second
    head = ws.entry_hash(base[-1])
    alone = ws.create(d[O1], WS, 'member', previous=head, issued_at=2000, subject=O2, role=None)
    assert ws.replay(WS, base + [alone], r, O1).state.owners() == {O1, O2}
    agreed = ws.cosign(alone, d[O2])
    assert ws.replay(WS, base + [agreed], r, O1).state.owners() == {O1}
    # an admin's co-signature is not an owner's
    base2 = base + [ws.create(d[O1], WS, 'member', previous=head, issued_at=1500, subject=AD, role='admin')]
    third = ws.create(d[O1], WS, 'member', previous=ws.entry_hash(base2[-1]), issued_at=2000, subject=M, role='owner')
    assert 'owner' not in ws.replay(WS, base2 + [ws.cosign(third, d[AD])], r, O1).state.members.get(M, '')


def test_fork_prefers_higher_role():
    r, d = world()
    base = chain(r, (d[O1], 'genesis', dict(subject=O1, role='owner')),
                 (d[O1], 'member', dict(subject=AD, role='admin')))
    head = ws.entry_hash(base[-1])
    early_admin = ws.create(d[AD], WS, 'member', previous=head, issued_at=1500, subject=M, role='member')
    owner = ws.create(d[O1], WS, 'member', previous=head, issued_at=1600, subject=AD, role=None)
    w = ws.replay(WS, base + [early_admin, owner], r, O1)
    assert w.head == ws.entry_hash(owner) and ws.entry_hash(early_admin) in w.superseded and AD not in w.state.members


def test_revoked_device_and_backdating():
    r, d = world()
    base = chain(r, (d[O1], 'genesis', dict(subject=O1, role='owner')),
                 (d[O1], 'member', dict(subject=AD, role='admin')))
    r.revoked[d[AD].device_id] = 1500
    late = ws.create(d[AD], WS, 'member', previous=ws.entry_hash(base[-1]), issued_at=1600, subject=M, role='member')
    back = ws.create(d[AD], WS, 'member', previous=ws.entry_hash(base[-1]), issued_at=1000, subject=M, role='member')
    w = ws.replay(WS, base + [late, back], r, O1)
    assert M not in w.state.members and {ws.entry_hash(late), ws.entry_hash(back)} <= set(w.ignored)


def test_shapes():
    d = DeviceIdentity()
    with pytest.raises(ValueError): ws.create(d, WS, 'settings', previous='aa' * 32, settings={'task_lease.max_ms': 1})
    with pytest.raises(ValueError): ws.create(d, WS, 'settings', previous='aa' * 32, settings={'other': 1})
    with pytest.raises(ValueError): ws.create(d, WS, 'member', subject=O1, role='owner')   # needs previous
    assert ws.settings_valid({'task_lease.default_ms': 120_000, 'task_lease.max_ms': 600_000})
    assert not ws.settings_valid({'task_lease.default_ms': 900_000, 'task_lease.max_ms': 600_000})


def test_real_registry_and_storage(tmp_path):
    state = BrokerState.create(['work']); storage = FilesystemStorage(tmp_path / 'replica')
    profile = state.trees[0].root.scope_id; me = state.account.account_id
    genesis = ws.create(state.device, profile, 'genesis', subject=me, role='owner')
    ws.publish(storage, genesis)
    storage.put(ws.PREFIX + profile + '/' + 'cd' * 32 + '.json', json.dumps(genesis).encode())   # wrong path: skipped
    w = ws.load(storage, profile, state.registry(), me)
    assert w.exists and w.state.members == {me: 'owner'} and ws.describe(w)['members'] == [dict(account=me, role='owner')]
    stranger = DeviceIdentity()
    fake = ws.create(stranger, profile, 'member', previous=w.head, subject='55' * 16, role='admin')
    ws.publish(storage, fake)
    assert ws.load(storage, profile, state.registry(), me).state.members == {me: 'owner'}
