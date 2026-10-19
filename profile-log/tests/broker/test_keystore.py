import os
import stat
import pytest
from agentmesh_log.broker.keystore import BrokerHome, BrokerState, FileKeyStore
from agentmesh_log.encoding import wire


def test_state(tmp_path):
    home = BrokerHome(tmp_path / 'home'); state = BrokerState.create(['work', 'personal'])
    tree = state.trees[0]; tree.rotate(tree.root.scope_id)
    store = FileKeyStore(home); store.save(state)
    restored = store.load()
    assert wire(restored.record()) == wire(state.record())
    assert len(restored.trees[0].epochs[tree.root.scope_id]) == 2
    if os.name != 'nt':
        assert stat.S_IMODE(home.root.stat().st_mode) == 0o700
        assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    before = store.path.read_bytes()
    assert store.fingerprint() == store.fingerprint()
    state.audit_key = os.urandom(32); store.save(state)
    assert before != store.path.read_bytes()


def test_atomic(tmp_path, monkeypatch):
    store = FileKeyStore(BrokerHome(tmp_path / 'home'))
    state = BrokerState.create(['work']); store.save(state)
    before = store.path.read_bytes()
    def fail(*args): raise OSError('injected')
    monkeypatch.setattr(os, 'replace', fail)
    with pytest.raises(OSError): store.save(state)
    assert store.path.read_bytes() == before
    assert not list(store.path.parent.glob('*.tmp'))


def test_unsafe_home(tmp_path):
    root = tmp_path / 'home'; root.mkdir(mode=0o755)
    if os.name != 'nt':
        root.chmod(0o755)
        with pytest.raises(ValueError): BrokerHome(root)
    link = tmp_path / 'link'
    try: link.symlink_to(root, target_is_directory=True)
    except OSError: return
    with pytest.raises(ValueError): BrokerHome(link)
