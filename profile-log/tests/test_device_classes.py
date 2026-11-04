from agentmesh_log.broker.keystore import BrokerState
from agentmesh_log.storage.filesystem import FilesystemStorage
from agentmesh_log.keyring import initialize_keyring
from agentmesh_log.identity import DeviceIdentity, sign, DEVICE_DOMAIN
from agentmesh_log.agekeys import DeviceAgeIdentity
from agentmesh_log.registry_store import (RegistryStore, device_record, device_class_record, device_class, now)
from agentmesh_log import workspace as ws


def two_devices(tmp_path):
    state = BrokerState.create(['work']); storage = FilesystemStorage(tmp_path / 'replica')
    initialize_keyring(state, storage)
    second = DeviceIdentity()
    cert = sign(dict(format='agentmesh-device-cert-v1', account_id=state.account.account_id, device_id=second.device_id,
                     device_public_key=second.public_bytes.hex(), issuer=state.device.device_id, issued_at=now()),
                state.device._private, DEVICE_DOMAIN)
    rs = RegistryStore(storage, state.account.public_record())
    rs.publish_device(device_record(second, cert, DeviceAgeIdentity()))
    return state, storage, rs, second


def test_class_rules(tmp_path):
    state, storage, rs, second = two_devices(tmp_path)
    first = state.device
    assert device_class(rs.load(), second.device_id) == 'workstation'          # no record: workstation
    rs.publish_device_class(device_class_record(second.device_id, first, 'server'))
    registry = rs.load()
    assert device_class(registry, second.device_id) == 'server'
    # an enrolled device cannot reclassify itself, and only a device's enroller classifies it
    rs.publish_device_class(device_class_record(second.device_id, second, 'workstation', issued_at=now() + 10))
    rs.publish_device_class(device_class_record(first.device_id, second, 'write-only', issued_at=now() + 20))
    registry = rs.load()
    assert device_class(registry, second.device_id) == 'server' and device_class(registry, first.device_id) == 'workstation'
    # the enroller may change it later; the newest valid record wins
    rs.publish_device_class(device_class_record(second.device_id, first, 'workstation', issued_at=now() + 30))
    assert device_class(rs.load(), second.device_id) == 'workstation'


def test_server_cannot_change_workspace(tmp_path):
    state, storage, rs, second = two_devices(tmp_path)
    rs.publish_device_class(device_class_record(second.device_id, state.device, 'server'))
    profile, me = state.trees[0].root.scope_id, state.account.account_id
    genesis = ws.create(state.device, profile, 'genesis', subject=me, role='owner')
    by_server = ws.create(second, profile, 'member', previous=ws.entry_hash(genesis), subject='55' * 16, role='admin')
    for e in (genesis, by_server): ws.publish(storage, e)
    w = ws.load(storage, profile, rs.load(), me)
    assert w.state.members == {me: 'owner'} and ws.entry_hash(by_server) in w.ignored
