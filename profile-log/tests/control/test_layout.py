import os
import stat
from agentmesh_log.control.layout import Layout, read_json, write_json


def test_layout_defaults_to_agentmesh_home(monkeypatch, tmp_path):
    monkeypatch.setenv('AGENTMESH_HOME', str(tmp_path))
    layout=Layout()
    assert layout.socket==tmp_path/'run'/'control.sock'
    assert layout.maintenance==tmp_path/'control'/'maintenance.json'
    assert layout.audit==tmp_path/'control'/'audit.jsonl'
    assert layout.status==tmp_path/'status'


def test_json_roundtrip_owner_only(tmp_path):
    layout=Layout(tmp_path).ensure()
    write_json(layout.quota,{'calls':3})
    assert read_json(layout.quota)=={'calls':3}
    if os.name!='nt':
        assert stat.S_IMODE(layout.quota.stat().st_mode)==0o600
        assert stat.S_IMODE(layout.control.stat().st_mode)==0o700


def test_read_json_bad_is_none(tmp_path):
    layout=Layout(tmp_path).ensure()
    assert read_json(layout.quota) is None
    layout.quota.write_text('[1]'); assert read_json(layout.quota) is None
    layout.quota.write_text('{'); assert read_json(layout.quota) is None
