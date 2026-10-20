import base64
import pytest
from agentmesh_log.oskeystore import OsKeyStore, initialize, migrate
from agentmesh_log.broker.keystore import BrokerHome,BrokerState,FileKeyStore
from agentmesh_log.identity import DeviceIdentity
from .test_lifecycle import yes


class FakeBackend:
    def __init__(self): self.items={}
    def load(self,account): return self.items.get(account)
    def store(self,account,key): self.items[account]=key
    def delete(self,account): self.items.pop(account,None)


def test_p4_10_encryption_tamper(tmp_path):
    home=BrokerHome(tmp_path/'a'); backend=FakeBackend(); state=BrokerState.create(['work']); store=OsKeyStore(home,backend)
    store.save(state); encrypted=store.path.read_bytes()
    for raw in (state.device.private_bytes(),state.account.private_bytes(),state.index_master_key):
        assert raw not in encrypted and raw.hex().encode() not in encrypted
    assert store.load().record()==state.record()
    original=backend.items[state.device.device_id]; backend.items[state.device.device_id]=b'x'*32
    with pytest.raises(ValueError): store.load()
    backend.items[state.device.device_id]=original
    store.path.write_bytes(encrypted[:-2]+b'xx')
    with pytest.raises(ValueError): store.load()


def test_p4_5_root_gone(tmp_path,monkeypatch):
    from agentmesh_log.model import Account
    roots=[]; original=Account.__init__
    def capture(self,*args,**kwargs): original(self,*args,**kwargs); roots.append(self.private_bytes())
    monkeypatch.setattr(Account,'__init__',capture)
    backend=FakeBackend(); home=BrokerHome(tmp_path/'init')
    shares=initialize(home,['work'],backend=backend)
    assert len(shares)==3
    state=OsKeyStore(home,backend).load()
    with pytest.raises(ValueError): state.account.private_bytes()
    d=DeviceIdentity(); state.registry().add_device(d.certificate(state.account,issuer=state.device))
    old=BrokerHome(tmp_path/'old'); plain=FileKeyStore(old); plain.save(BrokerState.create(['work']))
    assert len(migrate(old,backend=backend,console=yes()))==3
    assert not plain.path.exists()
    for target in (home,old):
        contents=[p.read_bytes() for p in target.root.rglob('*') if p.is_file()]
        contents.append(str(OsKeyStore(target,backend).load().record()).encode())
        for root in roots:
            for data in contents:
                assert all(value not in data for value in (root,root.hex().encode(),base64.urlsafe_b64encode(root).rstrip(b'=')))


def test_p4_10_migration_failure(tmp_path,monkeypatch):
    home=BrokerHome(tmp_path/'old'); plain=FileKeyStore(home); plain.save(BrokerState.create(['work']))
    def broken(self): raise ValueError('injected roundtrip failure')
    monkeypatch.setattr(OsKeyStore,'load',broken)
    with pytest.raises(ValueError): migrate(home,backend=FakeBackend(),console=yes())
    assert plain.path.exists()
