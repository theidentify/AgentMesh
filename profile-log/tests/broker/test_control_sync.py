"""Control mirroring must converge without repeated connector transfers."""
import pytest
from agentmesh_log import keyring
from agentmesh_log.broker.keystore import BrokerHome
from agentmesh_log.broker.service import Broker, StagedRemote
from agentmesh_log.oskeystore import initialize
from agentmesh_log.storage.filesystem import FilesystemStorage


class Remote(FilesystemStorage):
    def __init__(self, root):
        super().__init__(root)
        self.gets = []; self.puts = []; self.lists = []

    def list(self, prefix, **kwargs):
        self.lists.append(prefix)
        return super().list(prefix, **kwargs)

    def get(self, key):
        self.gets.append(key)
        return super().get(key)

    def put(self, key, data):
        self.puts.append(key)
        return super().put(key, data)


@pytest.fixture
def pair(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENTMESH_ALLOW_FILE_KEYSTORE', '1')
    home = BrokerHome(tmp_path/'home')
    initialize(home, ['work'], file_keystore=True)
    broker = Broker(home)
    remote = Remote(tmp_path/'remote')
    try:
        yield broker, remote
    finally:
        broker.close()


# Mirroring pins JSON bytes; signed-record validation remains a separate step.
CONNECTORS = ('connectors/app/policy/device.json',
              'connectors/app/secret/device.json',
              'connectors/inboxes/profile/scope/device.json')


def test_idle_connectors(pair):
    broker, remote = pair
    for key in CONNECTORS:
        broker.replica.storage.put(key, b'{}')
        remote.put(key, b'{}')
    for _ in range(3):
        remote.gets.clear(); remote.puts.clear()
        result = broker.sync_once(remote)
        assert result['errors'] == result['invalid'] == 0
        assert not set(CONNECTORS).intersection(remote.puts)
        assert not set(CONNECTORS).intersection(remote.gets)
        if _ > 0: assert result['uploaded'] == 0


def test_upload_once(pair):
    broker, remote = pair
    key = CONNECTORS[0]
    broker.replica.storage.put(key, b'{}')
    for _ in range(3):
        result = broker.sync_once(remote)
        assert result['errors'] == result['invalid'] == 0
        if _ > 0: assert result['uploaded'] == 0
    assert remote.puts.count(key) == 1
    assert remote.gets.count(key) == 0


def test_download_once(pair):
    broker, remote = pair
    key = CONNECTORS[0]
    remote.put(key, b'{}'); remote.puts.clear()
    for cycle in range(3):
        result = broker.sync_once(remote)
        assert result['errors'] == result['invalid'] == 0
        assert result['downloaded'] == (1 if cycle == 0 else 0)
        assert broker.replica.storage.get(key) == b'{}'
    assert remote.gets.count(key) == 1
    assert remote.puts.count(key) == 0
    assert key not in broker.replica.remote_keys
    assert key not in broker.replica.keys()


def test_excluded_controls(pair):
    broker, remote = pair
    excluded = ('connectors/gateway-state/remote.json',
                'registry/heartbeats/remote.json')
    for key in excluded: remote.put(key, b'{}')
    local = 'connectors/gateway-state/local.json'
    broker.replica.storage.put(local, b'{}')
    broker.replica.storage.put('registry/heartbeats/local.json', b'{}')
    remote.puts.clear()
    staged = StagedRemote(remote, broker.local_keys(), {'keys': set(), 'skip': set()})
    assert not set(excluded).intersection(staged.keys)
    assert not set(excluded).intersection(remote.gets)
    assert local not in broker.local_keys()
    assert 'registry/heartbeats/local.json' not in broker.local_keys()
    broker.sync_once(remote)
    assert not set(excluded).intersection(remote.gets)
    assert not remote.puts.count(local)
    assert not set(excluded).intersection(remote.puts)
    assert not set(excluded).intersection(broker._remote_keys)


def test_shared_prefixes(pair, monkeypatch):
    broker, remote = pair
    # Adding a control namespace must affect both consumers without another edit.
    prefixes = keyring.CONTROL_PREFIXES + ('future-control/',)
    monkeypatch.setattr(keyring, 'CONTROL_PREFIXES', prefixes)
    keyring.sync_control(broker.home, broker.store, remote)
    assert set(remote.lists) == set(prefixes)
    remote.lists.clear()
    StagedRemote(remote, set(), {'keys': set(), 'skip': set()})
    # keys/ is already supplied by the replica plan, never listed twice.
    assert set(remote.lists) | {'keys/'} == set(prefixes)
    assert len(remote.lists) == len(prefixes) - 1
    broker.replica.storage.put('future-control/object.json', b'{}')
    assert 'future-control/object.json' in broker.local_keys()


def test_converged_counts(pair):
    broker, remote = pair
    for key in CONNECTORS: broker.replica.storage.put(key, b'{}')
    broker.sync_once(remote)
    broker.sync_attempt(remote, 120)
    row = broker._sync_row
    assert row['outcome'] == 'ok'
    assert row['outbox'] == row['uploaded'] == 0
    assert row['local_objects'] == row['remote_objects'] == len(broker.local_keys())
    assert set(CONNECTORS) <= broker.local_keys()
