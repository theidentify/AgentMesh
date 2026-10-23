import pytest
from agentmesh_log.control.units import UNITS, get


def test_registry_matches_installed_labels():
    assert [u.label for u in UNITS]==['org.agentmesh.ingest','org.agentmesh.summary','org.agentmesh.retention',
        'org.agentmesh.profile-log.catch-up','org.agentmesh.profile-log.broker','org.agentmesh.profile-log.bridge',
        'org.agentmesh.insight','org.agentmesh.insight-tunnel']
    assert {u.name for u in UNITS if u.wrapped}=={'ingest','summary','retention'}
    assert all(u.max_runtime_s for u in UNITS if u.kind=='job')
    assert not any(u.max_runtime_s or u.wrapped for u in UNITS if u.kind=='service')


def test_get():
    assert get('broker').kind=='service'
    with pytest.raises(KeyError): get('sync')


def test_native_capable_units():
    assert {u.name for u in UNITS if u.native_ok}=={'ingest','summary','retention','catch-up'}
