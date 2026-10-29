import os
import stat
import threading
import time
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


@pytest.mark.parametrize('wait', [0,0.15], ids=['immediate','timeout'])
def test_lock_held(tmp_path, monkeypatch, wait):
    if wait==0:
        def no_sleep(seconds): raise AssertionError('immediate lock must not sleep')
        monkeypatch.setattr(time,'sleep',no_sleep)
    home=BrokerHome(tmp_path/'home'); path=home.root/'.lock'
    with home.lock():
        before=path.read_bytes(); start=time.monotonic()
        with pytest.raises(FileExistsError), home.lock(wait=wait): pass
        elapsed=time.monotonic()-start
        assert wait<=elapsed<wait+1
        assert path.read_bytes()==before
    assert not path.exists()


def test_lock_wait_release(tmp_path):
    home=BrokerHome(tmp_path/'home'); held=threading.Event()
    def holder():
        with home.lock():
            held.set(); time.sleep(0.2)
    thread=threading.Thread(target=holder); thread.start()
    try:
        assert held.wait(1)
        with home.lock(wait=2):
            assert (home.root/'.lock').read_bytes()==str(os.getpid()).encode()
            if os.name!='nt': assert stat.S_IMODE((home.root/'.lock').stat().st_mode)==0o600
    finally: thread.join(timeout=3)
    assert not thread.is_alive() and not (home.root/'.lock').exists()


@pytest.mark.parametrize('wait', [-1,float('nan'),float('inf'),True,'1',31],
    ids=['negative','nan','infinite','bool','string','too_large'])
def test_lock_invalid_wait(tmp_path, wait):
    home=BrokerHome(tmp_path/'home')
    with pytest.raises(ValueError), home.lock(wait=wait): pass
    assert not (home.root/'.lock').exists()


def test_lock_cleanup_on_error(tmp_path):
    home=BrokerHome(tmp_path/'home')
    with pytest.raises(RuntimeError), home.lock(wait=1): raise RuntimeError('injected')
    assert not (home.root/'.lock').exists()


def dead_pid():
    import subprocess, sys
    process=subprocess.Popen([sys.executable,'-c','pass']); process.wait()
    return process.pid


@pytest.mark.skipif(os.name=='nt', reason='stale-lock recovery is POSIX only')
def test_lock_dead_holder(tmp_path):
    home=BrokerHome(tmp_path/'home'); path=home.root/'.lock'
    path.write_bytes(str(dead_pid()).encode())
    with home.lock(): assert path.read_bytes()==str(os.getpid()).encode()
    assert not path.exists()


@pytest.mark.skipif(os.name=='nt', reason='stale-lock recovery is POSIX only')
def test_lock_before_boot(tmp_path, monkeypatch):
    # A lock that survived a reboot is stale even when its pid was reused by a live process.
    from agentmesh_log.broker import keystore
    home=BrokerHome(tmp_path/'home'); path=home.root/'.lock'
    path.write_bytes(str(os.getpid()).encode()); os.utime(path,(1000,1000))
    monkeypatch.setattr(keystore,'boot_time',lambda:2000)
    with home.lock(): pass
    assert not path.exists()


@pytest.mark.skipif(os.name=='nt', reason='stale-lock recovery is POSIX only')
@pytest.mark.parametrize('data', [b'', b'garbage', b'0'], ids=['empty','garbage','zero'])
def test_lock_bad_holder(tmp_path, monkeypatch, data):
    from agentmesh_log.broker import keystore
    monkeypatch.setattr(keystore,'boot_time',lambda:None)
    home=BrokerHome(tmp_path/'home'); path=home.root/'.lock'; path.write_bytes(data)
    with pytest.raises(FileExistsError), home.lock(): pass
    assert path.read_bytes()==data


def test_boot_time_sane():
    from agentmesh_log.broker.keystore import boot_time
    started=boot_time()
    if started is not None: assert 0<started<=time.time()
