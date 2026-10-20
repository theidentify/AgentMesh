"""Normal broker audit writes are allowed by amended relocation gate R-3."""
from pathlib import Path

import pytest


def test_broker_audit_append_changes_existing_protected_entry(tmp_path, monkeypatch):
    repository = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(repository / 'profile-log'))
    from agentmesh_log.broker.audit import Audit
    root = tmp_path / 'destination' / 'profile-log'
    root.mkdir(mode=0o700, parents=True)
    audit = Audit(root / 'audit.jsonl', b'synthetic-test-key')
    audit.append(agent_id='synthetic-agent', op='recall', outcome='ok')
    before = audit.path.stat().st_size
    audit.append(agent_id='synthetic-agent', op='recall', outcome='ok')
    assert audit.path.stat().st_size > before


def test_successful_broker_recall_modifies_existing_audit_file(tmp_path, monkeypatch):
    """Even an unchanged, empty recall writes within the existing broker tree."""
    pytest.importorskip('pyrage')
    repository = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(repository / 'profile-log'))
    monkeypatch.syspath_prepend(str(repository / 'profile-log' / 'tests'))
    from broker.support import setup_home
    from agentmesh_log.broker.service import Broker
    from profile_log_parity import local_client

    home, _, store, agents = setup_home(tmp_path)
    broker = Broker(home, store=store)
    client = local_client(broker, agents[0])
    try:
        request = dict(op='recall', query='synthetic relocation probe', compat=True, limit=1)
        assert 'error' not in client.request(request)
        audit = home.root / 'audit.jsonl'
        before = audit.stat().st_size
        assert 'error' not in client.request(request)
        assert audit.stat().st_size > before
    finally:
        client.close()
        broker.close()
