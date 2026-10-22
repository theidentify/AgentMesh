"""Legacy peer tests opt in without reading private settings or the real home."""
import json
import pytest


@pytest.fixture(autouse=True)
def packet_test_home(tmp_path_factory,monkeypatch,request):
    root=tmp_path_factory.mktemp('insight-packet-home')
    monkeypatch.setenv('AGENTMESH_HOME',str(root))
    if not request.node.path.name.startswith('test_phase7'):
        (root/'packet-sync-status.json').write_text(json.dumps({
            'format':'agentmesh-packet-sync-status-v1','packet_sync':'enabled'}),encoding='utf-8')
