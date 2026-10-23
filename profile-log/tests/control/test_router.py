from agentmesh_log.control.router import Router
from agentmesh_log.control.store import Store
from agentmesh_log.control.units import get
from .support import FakeBackend


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
