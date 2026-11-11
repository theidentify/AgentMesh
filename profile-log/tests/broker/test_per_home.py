"""Per-home storage (J1b): each home writes only homes/<device>/, every home reads all areas."""
import json
import pytest
from .test_federation import run, write, recall, agent, open_windows
from agentmesh_log.storage.areas import AreaStorage, wrap
from agentmesh_log.storage.filesystem import FilesystemStorage


@pytest.fixture(autouse=True)
def private_home(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path / 'user')); monkeypatch.setenv('USERPROFILE', str(tmp_path / 'user'))
    monkeypatch.setenv('AGENTMESH_HOME', str(tmp_path / 'am'))


def test_area_storage(tmp_path):
    base = FilesystemStorage(tmp_path / 'r')
    a, b = AreaStorage(base, 'aa' * 16), AreaStorage(base, 'bb' * 16)
    a.put('events/x/1.json', b'1'); b.put('events/x/2.json', b'2'); b.put('events/x/1.json', b'1')
    assert sorted(base.list('')) == ['homes/' + 'aa' * 16 + '/events/x/1.json', 'homes/' + 'bb' * 16 + '/events/x/1.json',
                                     'homes/' + 'bb' * 16 + '/events/x/2.json']
    assert list(a.list('events/')) == ['events/x/1.json', 'events/x/2.json']   # union, no duplicates
    assert list(a.list('events/', start_after='events/x/1.json')) == ['events/x/2.json']
    assert a.get('events/x/2.json') == b'2' and a.get('events/x/1.json') == b'1'
    fresh = AreaStorage(base, 'cc' * 16)
    assert fresh.get('events/x/2.json') == b'2'   # probes areas without a listing
    from agentmesh_log.storage import ObjectNotFound
    with pytest.raises(ObjectNotFound): fresh.get('events/x/3.json')
    assert wrap(base, 'flat', None) is base and wrap(base, None, None) is base
    assert isinstance(wrap(base, 'per-home', 'aa' * 16), AreaStorage)
    with pytest.raises(ValueError): AreaStorage(base, '../escape')
    assert base.folders('homes/') == ['aa' * 16, 'bb' * 16] and base.folders('nothing/') == []


def sync(home, remote): run('broker', 'sync', '--home', home, '--filesystem', remote, '--layout', 'per-home')


def device(home): return run('workspace', 'account', '--home', home)['device']


def test_two_homes_own_areas(tmp_path):
    alice, remote = tmp_path / 'alice', tmp_path / 'remote'
    run('broker', 'init', '--home', alice, '--profile', 'work')
    run('broker', 'scope', 'add', '--home', alice, 'work/mesh')
    run('workspace', 'init', '--home', alice, '--profile', 'work')
    sync(alice, remote)
    link = tmp_path / 'bob.link'
    made = run('workspace', 'invite', 'create', '--home', alice, '--profile', 'work', '--scope', 'work/mesh', '--link', link,
               '--filesystem', remote, '--layout', 'per-home')
    from agentmesh_log import invites
    assert invites.decode_link(link.read_text())['remote'] == {'kind': 'filesystem', 'layout': 'per-home'}
    sync(alice, remote)
    bob = tmp_path / 'bob'
    run('workspace', 'join-link', '--link-file', link, '--home', bob, '--folder', remote, '--recovery-out', tmp_path / 'rb', '--no-rules')
    run('workspace', 'invite', 'accept', '--home', alice, '--profile', 'work', '--request', made['slot'], '--filesystem', remote)
    sync(alice, remote)
    done = run('workspace', 'join-link', '--link-file', link, '--home', bob, '--folder', remote)
    assert done['state'] == 'ready'
    assert json.loads((bob / 'remote.json').read_text())['layout'] == 'per-home'
    # nothing outside the areas except the join slots
    top = {k.split('/', 1)[0] for k in FilesystemStorage(remote).list('')}
    assert top == {'homes', 'join'}
    a_dev, b_dev = device(alice), device(bob)
    # Bob's join records went up through Alice's area when she approved him, so he has no area until he writes
    assert set(FilesystemStorage(remote).folders('homes/')) <= {a_dev, b_dev} and a_dev in FilesystemStorage(remote).folders('homes/')
    # Bob's agent writes; the event lands only in Bob's area, and Alice reads it from there
    from agentmesh_log.encoding import parse
    from agentmesh_log.identity import AgentIdentity
    from pathlib import Path
    record = parse(Path(done['agent_key']).read_bytes())
    bob_agent = AgentIdentity.from_private(record['agent_cert'], bytes.fromhex(record['agent_private_key']))
    event = write(bob, bob_agent, 'work/mesh', 'written in my own area')
    run('broker', 'sync', '--home', bob)   # remote.json alone selects the per-home layout
    keys = list(FilesystemStorage(remote).list('homes/'))
    mine = [k for k in keys if k.endswith(event['id'] + '.json')]
    assert len(mine) == 1 and mine[0].startswith('homes/' + b_dev + '/events/')
    assert set(FilesystemStorage(remote).folders('homes/')) == {a_dev, b_dev}
    sync(alice, remote)
    open_windows(alice)
    assert 'written in my own area' in recall(alice, agent(alice, 'work/mesh', 'alice-agent'), 'own area')
    # a second sync uploads nothing new: objects found in any area count as present
    before = sorted(FilesystemStorage(remote).list(''))
    sync(alice, remote); run('broker', 'sync', '--home', bob)
    assert sorted(FilesystemStorage(remote).list('')) == before


def test_s3_folders():
    from agentmesh_log.storage.s3 import S3Storage
    calls = []
    class Paginator:
        def paginate(self, **kw):
            calls.append(kw)
            return [{'CommonPrefixes': [{'Prefix': 'ws/homes/' + 'aa' * 16 + '/'}, {'Prefix': 'ws/homes/' + 'bb' * 16 + '/'}]}]
    class Client:
        meta = None
        def get_paginator(self, name): assert name == 'list_objects_v2'; return Paginator()
    store = S3Storage('bucket', prefix='ws', client=Client())
    assert store.folders('homes/') == ['aa' * 16, 'bb' * 16]
    assert calls == [dict(Bucket='bucket', Prefix='ws/homes/', Delimiter='/')]
