import pytest
from agentmesh_log.agekeys import DeviceAgeIdentity
from agentmesh_log.registry_store import RegistryStore, device_record, revocation
from agentmesh_log.identity import DeviceIdentity, AgentIdentity, Registry
from agentmesh_log.model import Account
from agentmesh_log.storage.filesystem import FilesystemStorage


def test_age_roundtrip():
    import pyrage
    age = DeviceAgeIdentity.from_raw(bytes(range(32)))
    assert DeviceAgeIdentity.parse(age.secret()).recipient == age.recipient
    assert age.raw() == bytes(range(32))
    encrypted = pyrage.encrypt(b'disposable', [pyrage.x25519.Recipient.from_str(age.recipient)])
    assert age.decrypt(encrypted) == b'disposable'
    with pytest.raises(ValueError): DeviceAgeIdentity.parse(age.secret()[:-1] + 'Q')


def test_p4_4_registry_revocation(tmp_path):
    account = Account(); a, b, c = DeviceIdentity(), DeviceIdentity(), DeviceIdentity()
    store = RegistryStore(FilesystemStorage(tmp_path), account.public_record())
    for d, cert in [(a,a.certificate(account,issued_at=1)),(b,b.certificate(account,issuer=a,issued_at=2))]:
        store.publish_device(device_record(d, cert, DeviceAgeIdentity(), issued_at=3))
    agent = AgentIdentity(b,account.account_id,issued_at=4)
    assert store.load().verify_agent(agent.certificate()) == agent.public_bytes
    for signer in (b,c):
        with pytest.raises(ValueError): store.publish_revocation(revocation(b.device_id,signer,revoked_at=10))
    store.publish_revocation(revocation(b.device_id,a,revoked_at=10))
    registry = store.load()
    with pytest.raises(ValueError): registry.verify_agent(agent.certificate())
    with pytest.raises(ValueError): registry.add_device(c.certificate(account,issuer=b,issued_at=11))
    registry.add_device(c.certificate(account,issuer=b,issued_at=9))
    with pytest.raises(ValueError): store.publish_revocation(revocation(a.device_id,b,revoked_at=12))


def test_device_age_binding(tmp_path):
    account = Account(); d = DeviceIdentity()
    store = RegistryStore(FilesystemStorage(tmp_path),account.public_record())
    record = device_record(d,d.certificate(account),DeviceAgeIdentity())
    record['age_recipient'] = DeviceAgeIdentity().recipient
    with pytest.raises(ValueError): store.publish_device(record)
