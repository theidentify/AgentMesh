from agentmesh_log.control.router import Router
from agentmesh_log.control.store import Store
from agentmesh_log.control.units import get
import json
from agentmesh_log.control import migrate
from agentmesh_log.control.health import observe
from agentmesh_log.control.layout import Layout
from agentmesh_log.control.units import UNITS
from .support import FakeBackend, FakeLaunchd


def test_routes_by_store(tmp_path):
    store=Store(tmp_path/'state.db'); launchd,native=FakeBackend(),FakeBackend()
    router=Router(store,launchd,native); unit=get('ingest')
    router.restart(unit); assert launchd.calls==[('restart','ingest')] and native.calls==[]
    assert router.status(unit)['backend']=='launchd'
    store.set_backend('ingest','native',{},1)
    router.start(unit); router.stop(unit)
    assert native.calls==[('start','ingest'),('stop','ingest')] and router.status(unit)['backend']=='native'
    native.failed.add('ingest')
    assert router.status(unit)['failed'] is True and router.reset(unit) is True and router.reset(unit) is False


def test_reload_conflict_until_evicted(tmp_path):
    layout=Layout(tmp_path).ensure(); store=Store(layout.state); launchd=FakeLaunchd()
    store.set_backend('ingest','native',{},1); launchd.loaded.discard('ingest')  # an M2 adopt: bootout, no disable
    router=Router(store,launchd,FakeBackend()); health=lambda:{r['name']:r['health'] for r in observe(layout,router,UNITS)}
    assert router.status(get('ingest'))['conflict'] is False and health()['ingest']=='idle'
    launchd.reboot()
    assert router.status(get('ingest'))['conflict'] is True and health()['ingest']=='conflict'
    assert router.status(get('summary'))['conflict'] is False and health()['summary']=='idle'
    assert migrate.evict_reloaded(layout,store,launchd)==['ingest'] and health()['ingest']=='idle'
    assert json.loads(layout.audit.read_bytes().splitlines()[-1])['op']=='launchd_reloaded'
