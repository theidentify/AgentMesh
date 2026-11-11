"""J3: the supervisor runs one broker per joined workspace home and retires it when the home goes away."""
import json
import os
import sys
import pytest
from agentmesh_log.control.layout import Layout
from agentmesh_log.control.native import NativeBackend
from agentmesh_log.control.store import Store
from agentmesh_log.control.units import UNITS
from agentmesh_log.control import workspaces


def home(root, id8, state='ready', keystore=True, remote=True):
    h = root / 'ws' / id8; h.mkdir(parents=True)
    if keystore: (h / 'keystore.json').write_text('{}')
    if remote: (h / 'remote.json').write_text('{"filesystem": "/shared", "layout": "per-home"}')
    if state: (h / 'joins.json').write_text(json.dumps([{'workspace': id8 * 4, 'state': state}]))
    return h


def setup(tmp_path):
    layout = Layout(tmp_path / 'am'); layout.ensure()
    store = Store(tmp_path / 'state.db'); units = list(UNITS)
    native = NativeBackend(store, units, environ={'HOME': str(tmp_path)})
    return layout, store, native, units


def test_ready_homes_only(tmp_path):
    layout, store, native, units = setup(tmp_path)
    ready = home(layout.root, 'aaaaaaaa')
    home(layout.root, 'bbbbbbbb', state='waiting_for_admin')
    home(layout.root, 'cccccccc', remote=False)
    home(layout.root, 'dddddddd', keystore=False)
    (layout.root / 'ws' / 'not-a-home').mkdir()
    result = workspaces.attach(layout, store, native, units, python='/venv/bin/python')
    assert result == {'added': ['ws-aaaaaaaa'], 'retired': []}
    spec = store.spec('ws-aaaaaaaa')
    assert store.backend('ws-aaaaaaaa') == 'native'
    assert spec['argv'] == ['/venv/bin/python', '-m', 'agentmesh_log.cli', 'broker', 'run', '--home', str(ready), '--sync-interval', '120']
    assert spec['keep_alive'] is True and spec['umask'] == 0o77 and spec['stderr'].endswith('logs/ws-aaaaaaaa.err.log')
    assert not any(a.startswith('--s3') or a == '--filesystem' for a in spec['argv'])   # the remote stays in remote.json
    assert 'ws-aaaaaaaa' in native.units and any(u.name == 'ws-aaaaaaaa' for u in units)
    # a second scan changes nothing
    assert workspaces.attach(layout, store, native, units, python='/venv/bin/python') == {'added': [], 'retired': []}


def test_removed_home(tmp_path):
    layout, store, native, units = setup(tmp_path)
    h = home(layout.root, 'aaaaaaaa')
    workspaces.attach(layout, store, native, units, python='/venv/bin/python')
    (h / 'joins.json').unlink(); (h / 'remote.json').unlink(); (h / 'keystore.json').unlink(); h.rmdir()
    assert workspaces.attach(layout, store, native, units) == {'added': [], 'retired': ['ws-aaaaaaaa']}
    assert store.backend('ws-aaaaaaaa') == workspaces.RETIRED and 'ws-aaaaaaaa' not in native.store.native_units()
    assert 'ws-aaaaaaaa' not in native.units and not any(u.name == 'ws-aaaaaaaa' for u in units)
    # a new supervisor whose store still names a vanished home's unit retires it too
    store.set_backend('ws-eeeeeeee', 'native', workspaces.spec_for(layout, layout.root / 'ws' / 'eeeeeeee', '/p'), 1)
    fresh_units = list(UNITS); fresh = NativeBackend(store, fresh_units, environ={'HOME': str(tmp_path)})
    assert workspaces.attach(layout, store, fresh, fresh_units)['retired'] == ['ws-eeeeeeee']


def test_ticker_rate(tmp_path):
    layout, store, native, units = setup(tmp_path)
    clock = [0.0]
    tick = workspaces.ticker(layout, store, native, units, python='/p', clock=lambda: clock[0])
    assert tick() == {'added': [], 'retired': []}
    home(layout.root, 'aaaaaaaa')
    clock[0] = 30; assert tick() is None
    clock[0] = 61; assert tick() == {'added': ['ws-aaaaaaaa'], 'retired': []}


@pytest.mark.skipif(os.name == 'nt', reason='POSIX process groups')
def test_tick_starts_broker(tmp_path):
    """The next supervisor tick starts the registered unit (a stand-in program that records its argv)."""
    layout, store, native, units = setup(tmp_path)
    h = home(layout.root, 'aaaaaaaa')
    marker = tmp_path / 'argv.json'
    fake = tmp_path / 'fake_python.py'
    fake.write_text('import json,sys\njson.dump(sys.argv[1:], open(%r, "w"))\n' % str(marker))
    workspaces.attach(layout, store, native, units, python=sys.executable)
    spec = store.spec('ws-aaaaaaaa'); spec['argv'] = [sys.executable, str(fake)] + spec['argv'][1:]
    store.set_backend('ws-aaaaaaaa', 'native', spec, 2)
    native.tick()
    run = native.running.get('ws-aaaaaaaa'); assert run is not None
    run['proc'].wait(10); native.tick()
    assert json.loads(marker.read_text())[-4:] == ['--home', str(h), '--sync-interval', '120']
    native.shutdown()
