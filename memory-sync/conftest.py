"""Legacy packet tests opt in; Phase 7 tests exercise the default-off policy."""
import json
import pytest


@pytest.fixture(autouse=True)
def packet_test_home(tmp_path_factory,monkeypatch,request):
    root=tmp_path_factory.mktemp('packet-test-home')
    monkeypatch.setenv('AGENTMESH_HOME',str(root))
    if not request.node.path.name.startswith('test_phase7'):
        path=root/'settings.local.json'
        path.write_text(json.dumps({'packet_sync':{'enabled':True}}),encoding='utf-8'); path.chmod(0o600)
