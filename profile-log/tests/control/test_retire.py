"""SQLite retirement R4: the sqlite-retire ceremony, its rollback and the archive, against a synthetic staging setup.

The staging database is real (memory-sync's own init, ingest and digest); the broker runs in process and one test
agent plays both the importer and the summarizer.
"""
import json
import os
from pathlib import Path
import shutil
import stat
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT/'memory-sync'))
from broker.support import setup_home
from broker.test_pages import Local
from agentmesh_log.broker.service import Broker
from agentmesh_log.control import maintenance, retire
from agentmesh_log.control.layout import Layout

pytestmark = pytest.mark.skipif(os.name == 'nt', reason='the ceremony is Mac-only (launchd jobs, Touch ID)')


class Client(Local):
    """The in-process client with the importer's signing loop, as BrokerClient.import_row does it."""
    def import_row(self, **fields):
        from agentmesh_log.encoding import typed
        from agentmesh_log.events import EVENT_DOMAIN
        response = self.request(dict(op='import', **fields))
        while response.get('stage') == 'sign':
            response = self.request(dict(op='sign', signature=self.agent._private.sign(EVENT_DOMAIN+typed(response['unsigned'])).hex()))
        return response
    def close(self): pass


class Yes:
    def __init__(self, answer=True): self.answer, self.asked = answer, []
    def confirm(self, action, summary): self.asked.append(action); return self.answer


def record(ident, text):
    return json.dumps({'type': 'message', 'id': ident, 'timestamp': '2026-11-02T00:00:01Z',
                       'message': {'role': 'user', 'content': [{'type': 'text', 'text': text}]}}) + '\n'


def extractor(request, *, model):
    seen = json.loads(request['input'][0]['content'])
    items = [dict(memory_key='project.demo.fact.' + e['content'].split()[0], kind='fact', scope='project', scope_key='demo',
                  project='demo', content=e['content'], status='active', confidence=0.8,
                  evidence=[dict(event_id=e['id'], quote=e['content'])]) for e in seen['events']]
    return dict(payload=dict(items=items, reviewed_event_ids=[e['id'] for e in seen['events']], conflicts=[]),
                provider='fixture', model=model, api_calls=1, usage={})


@pytest.fixture
def staging(tmp_path):
    import memory_sync, sqlite_memory, ingest_sessions, bounded_digest
    from agentmesh_log.migrate.source import staging_node
    home, state, store, agents = setup_home(tmp_path/'broker')
    grant = state.grants[agents[0].agent_id]; grant.allow_import = True; grant.allow_summarize = True; store.save(state)
    broker = Broker(home, store=store)
    root = tmp_path/'am'
    (root/'jobs').mkdir(parents=True); (root/'agents').mkdir(); (root/'data').mkdir()
    folder = root/'omp'/'-Workspaces-demo'; folder.mkdir(parents=True)
    (folder/'a.jsonl').write_text(record('a1', 'alpha one') + record('a2', 'beta two'))
    db = root/'data'/'staging.db'
    sqlite_memory.init_database(db); memory_sync.initialize(db, 'mac', '9a95369f-97c5-4b0e-bf41-f110ab1450a9')
    ingest_sessions.ingest(db, roots={'omp': root/'omp'})
    bounded_digest.run_once(db, extractor=extractor, model='m', max_events=1)  # one memory; one event still waiting
    export = root/'export'; export.mkdir(); (export/'_Index.md').write_text('plaintext projection')
    mapping = root/'migration-mapping.json'
    mapping.write_text(json.dumps(dict(format='agentmesh-migration-mapping-v1', projects={'demo': 'work/a'}, prefixes=[],
                                       null_project='work/a', **{'global': 'work/a'})))
    bridge = Path(str(db) + '.profile-log.json')
    bridge.write_text(json.dumps(dict(format='agentmesh-profile-log-bridge-v1', enabled=True, recall=True, dual_write=True,
        authority=True, cli_argv=['/usr/bin/python3', '-m', 'agentmesh_log.cli'], agent_key=str(root/'agents/recall.key'),
        import_agent_key=str(root/'agents/importer.key'), mapping=str(mapping), profiles=['work'], timeout_seconds=10,
        recall_fallback=False, device_id=state.device.device_id, staging_node=staging_node(db))))
    os.chmod(bridge, 0o600)
    production = root/'jobs'/'agentmesh-production.json'
    production.write_text(json.dumps(dict(primary_backend='sqlite', retention='disabled', database=str(db),
        runtime=str(ROOT/'memory-sync'), export_root=str(export), status_dir=str(root/'status'),
        roots={'omp': str(root/'omp')}, summary={})))
    shutil.copy(ROOT/'memory-sync/jobs/agentmesh_production.py', root/'jobs')
    (root/'agents'/'summarizer.key').write_text('{}')
    clients = lambda path: Client(broker, agents[0])
    return dict(root=root, db=db, bridge=bridge, production=production, export=export, clients=clients, broker=broker,
                layout=Layout(root))


def ceremony(s, console=None, **kw):
    setup = retire.Setup(s['root'])
    return retire.retire(s['layout'], setup, console=console or Yes(), helper=None, client_factory=s['clients'], first=False, **kw)


def test_dry_run_changes_nothing(staging):
    s = staging
    before = s['production'].read_text()
    result = retire.plan(retire.Setup(s['root']), client_factory=s['clients'])
    assert result['problems'] == []
    assert (result['seed']['transcripts'], result['seed']['waiting_events']) == (1, 1)
    assert {a['name'] for a in result['archive']} >= {'staging.db', 'export'}
    assert s['production'].read_text() == before and s['db'].exists()


def test_retire_rollback_delete(staging):
    s = staging
    console = Yes()
    result = ceremony(s, console)
    assert result.get('ok'), result
    assert console.asked == ['migrate']
    archive = s['root']/'backups'/'archive'/result['archive']
    # The staging files moved into an owner-only archive, nothing left behind.
    assert not s['db'].exists() and not s['export'].exists()
    assert stat.S_IMODE(archive.stat().st_mode) == 0o700
    assert stat.S_IMODE((archive/'staging.db').stat().st_mode) == 0o400
    assert stat.S_IMODE((archive/'export'/'_Index.md').stat().st_mode) == 0o400
    assert (archive/'README.md').is_file() and (archive/'config'/'bridge.json').is_file()
    # Switched: broker backend, importer quiet, summarizer state seeded, maintenance off again.
    config = json.loads(s['production'].read_text())
    assert config['backend'] == 'broker' and config['broker']['state_dir'] == str(s['root']/'jobs')
    bridge = json.loads(s['bridge'].read_text())
    assert (bridge['dual_write'], bridge['authority'], bridge['recall']) == (False, False, True)
    assert all((s['root']/'jobs'/n).is_file() for n in retire.STATE_FILES)
    assert not maintenance.status(s['layout'])['on']
    # The final import put the SQLite memory into the profile log.
    heads = s['clients'](None).request(dict(op='heads', scope='work/a'))['heads']
    assert [h['content'] for h in heads.values()] == ['alpha one']
    # Retiring again is refused; the archive is listed with its rollback window open.
    assert ceremony(s)['error'] == 'preflight_failed'
    assert retire.archives(s['root'])[0]['rollback_open'] is True
    rolled = retire.rollback(s['layout'], s['root'], result['archive'], console=Yes(), helper=None)
    assert rolled.get('ok'), rolled
    assert s['db'].is_file() and (s['export']/'_Index.md').read_text() == 'plaintext projection'
    assert stat.S_IMODE(s['db'].stat().st_mode) == 0o600
    assert json.loads(s['production'].read_text()).get('backend', 'sqlite') == 'sqlite'
    assert json.loads(s['bridge'].read_text())['authority'] is True
    assert not any((s['root']/'jobs'/n).exists() for n in retire.STATE_FILES)
    assert (archive/'rolled-back-state'/'summary-state.json').is_file()
    assert retire.delete(s['layout'], s['root'], result['archive'], console=Yes(), helper=None)['error'] == 'rollback_window_open'
    assert retire.delete(s['layout'], s['root'], result['archive'], console=Yes(False), helper=None, early=True)['error'] == 'presence_required'
    assert retire.delete(s['layout'], s['root'], result['archive'], console=Yes(), helper=None, early=True) == dict(ok=True, deleted=result['archive'])
    assert not archive.exists()


def test_presence_refused_changes_nothing(staging):
    s = staging
    assert ceremony(s, Yes(False)) == dict(error='presence_required')
    assert s['db'].exists() and json.loads(s['production'].read_text())['primary_backend'] == 'sqlite'
    assert not (s['root']/'backups').exists()


def test_verify_gap_aborts_and_restores(staging, monkeypatch):
    s = staging
    import agentmesh_log.migrate.verify as verify
    monkeypatch.setattr(verify, 'verify', lambda *a, **k: dict(missing=1, retired_but_present=0))
    before = (s['production'].read_text(), s['bridge'].read_text())
    result = ceremony(s)
    assert result == dict(error='aborted', reason='verify found 1 missing, 0 retired but present', restored=True)
    assert (s['production'].read_text(), s['bridge'].read_text()) == before
    assert s['db'].exists() and s['export'].exists() and not any((s['root']/'jobs'/n).exists() for n in retire.STATE_FILES)
    assert not (s['root']/'backups'/'archive').exists() or not list((s['root']/'backups'/'archive').iterdir())
    assert not maintenance.status(s['layout'])['on']


def test_failure_after_switch_restores(staging, monkeypatch):
    s = staging
    import sqlite_cutover
    monkeypatch.setattr(sqlite_cutover, 'checkpoint', lambda db: (_ for _ in ()).throw(ValueError('staging database busy')))
    before = (s['production'].read_text(), s['bridge'].read_text())
    assert ceremony(s)['error'] == 'aborted'
    assert (s['production'].read_text(), s['bridge'].read_text()) == before
    assert s['db'].exists() and not any((s['root']/'jobs'/n).exists() for n in retire.STATE_FILES)


def test_preflight_lists_problems(tmp_path):
    result = retire.plan(retire.Setup(tmp_path))
    assert 'production config missing or invalid' in result['problems']
    assert any('summarizer agent key missing' in p for p in result['problems'])


def test_cli_dry_run_and_archive_list(staging):
    from agentmesh_log.cli import parser
    from agentmesh_log.control.cli import execute
    s = staging
    result = execute(parser().parse_args(['control', 'sqlite-retire']), layout=s['layout'])
    # The placeholder key reaches no broker, so the dry run reports it and changes nothing.
    assert result['problems'] == ['summarizer agent unavailable: ValidationError'] and result['seed']['waiting_events'] == 1
    assert execute(parser().parse_args(['control', 'sqlite-archive', 'list']), layout=s['layout']) == {'archives': []}
    assert execute(parser().parse_args(['control', 'sqlite-archive', 'delete']), layout=s['layout']) == {'error': 'archive_required'}
    assert s['db'].exists()
