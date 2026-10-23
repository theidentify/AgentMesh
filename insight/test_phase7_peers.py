import json
from pathlib import Path
from unittest.mock import patch

import adapters


class MissingReader:
    label = 'SQLite staging'
    prefix = ''

    def connect(self):
        raise RuntimeError('unused')


def test_p7_retired_peer_panes_ignore_packet_status(tmp_path):
    root = tmp_path/'agentmesh'
    status = root/'exchange/status'
    status.mkdir(parents=True)
    (status/'mac.json').write_text(json.dumps({'format':'agentmesh-status-v1', 'node':'mac', 'counts':{'memory_items':123}}))
    result = adapters.peer_status(agentmesh_home=root)
    assert result['rows'] == []
    assert result['packet_sync'] == 'retired'
    assert 'packet sync retired' in result['note']


def test_p7_retired_operations_never_observe_syncthing(tmp_path):
    root = tmp_path/'agentmesh'
    with patch('syncthing_transport.observe', side_effect=AssertionError('must not observe')):
        value = adapters.operations(MissingReader(), None, agentmesh_home=root)
    assert value['sync']['transport'] == 'packet sync retired'
    assert value['sync']['receipts'] is None
    assert value['sync']['outbox'] == []
    assert value['sync']['error'] == 'packet sync retired'
    assert value['ingest'] == adapters.unavailable('Ingest counters')


def test_p7_enabled_packet_peer_view_and_override(tmp_path):
    root = tmp_path/'agentmesh'
    root.mkdir()
    path=root/'packet-sync-status.json'
    path.write_text(json.dumps(dict(format='agentmesh-packet-sync-status-v1',packet_sync='enabled')),encoding='utf-8')
    assert len(adapters.peer_status(agentmesh_home=root)['rows']) == 2
    path.write_text(json.dumps(dict(format='agentmesh-packet-sync-status-v1',packet_sync='retired')),encoding='utf-8')
    assert adapters.peer_status(agentmesh_home=root)['rows'] == []
