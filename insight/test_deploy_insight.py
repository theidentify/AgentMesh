"""The owner-run deploy only against scratch copies: dry run, apply, rollback."""
import json
import plistlib
import sqlite3
import subprocess
from pathlib import Path

import pytest

import adapters
import deploy_insight

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def scratch(tmp_path):
    am, hermes, trash = tmp_path/'agentmesh', tmp_path/'hermes', tmp_path/'Trash'
    for d in (am/'status/insight-release-old', hermes/'cron', hermes/'omp-memory', trash): d.mkdir(parents=True)
    audit = [dict(job_id=deploy_insight.JOB, fire_id='f1', ts='2026-10-01T00:00:10Z', duration_ms=10000, prompt_tokens=7,
                  completion_tokens=2, model='m', error='PRIVATE traceback', deliver_target='PRIVATE'),
             dict(job_id='other', fire_id='x', ts='2026-10-01T00:00:00Z', prompt_tokens=99)]
    (hermes/'cron/usage_audit.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in audit))
    (hermes/'cron/jobs.json').write_text('{"jobs":[]}')
    with sqlite3.connect(hermes/'state.db') as c:
        c.execute('CREATE TABLE sessions(id TEXT, started_at REAL, input_tokens INT, api_call_count INT, model TEXT, title TEXT)')
        c.execute("INSERT INTO sessions VALUES ('cron_%s_1', 1790812800, 5, 1, 'm', 'PRIVATE title')" % deploy_insight.JOB)
        c.execute("INSERT INTO sessions VALUES ('chat_1', 1790812800, 50, 1, 'm', 'PRIVATE chat')")
    (hermes/'omp-memory/summary-metrics.jsonl').write_text(json.dumps(dict(record_type='run', run_id='stray', status='committed'))+'\n')
    (hermes/'omp-memory/summary-metrics.jsonl.bounded.lock').write_text('')
    plist = tmp_path/'org.agentmesh.insight.plist'
    old = am/'status/insight-release-old'
    plist.write_bytes(plistlib.dumps({'Label':'org.agentmesh.insight', 'WorkingDirectory':str(old),
        'EnvironmentVariables':{'HERMES_HOME':str(hermes), 'INSIGHT_PRIMARY_BACKEND':'sqlite'},
        'ProgramArguments':['/venv/python', '-u', str(old/'server.py'), '--port', '51877']}))
    rev = subprocess.run(['git', '-C', str(REPO), 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()
    args = ['--rev', rev, '--repo', str(REPO), '--agentmesh-home', str(am), '--hermes-home', str(hermes),
            '--plist', str(plist), '--trash', str(trash), '--launch-agents', str(tmp_path/'LaunchAgents'), '--no-restart']
    (tmp_path/'LaunchAgents').mkdir()
    return dict(am=am, hermes=hermes, trash=trash, plist=plist, rev=rev, args=args, old=old, agents=tmp_path/'LaunchAgents')


def test_refuses_while_a_job_still_runs_in_omp_memory(scratch):
    """The ingest/summary jobs must leave omp-memory first (PR #103), or launchd cannot spawn them."""
    job = scratch['agents']/'org.agentmesh.summary.plist'
    job.write_bytes(plistlib.dumps({'Label':'org.agentmesh.summary', 'WorkingDirectory':str(scratch['hermes']/'omp-memory')}))
    (scratch['agents']/'broken.plist').write_text('not a plist')
    before = snapshot(scratch['am'].parent)
    for extra in ([], ['--apply']):
        with pytest.raises(SystemExit, match='org.agentmesh.summary.plist'):
            deploy_insight.main(scratch['args']+extra)
    assert snapshot(scratch['am'].parent) == before
    job.write_bytes(plistlib.dumps({'Label':'org.agentmesh.summary', 'WorkingDirectory':str(scratch['am']/'jobs')}))
    assert deploy_insight.main(scratch['args']+['--apply']) == 0
    assert not (scratch['hermes']/'omp-memory').exists()


def snapshot(root):
    return sorted((str(p.relative_to(root)), p.read_bytes() if p.is_file() else None) for p in root.rglob('*'))


def test_dry_run_changes_nothing(scratch, capsys):
    root = scratch['am'].parent
    before = snapshot(root)
    assert deploy_insight.main(scratch['args']) == 0
    assert snapshot(root) == before
    out = capsys.readouterr().out
    assert 'Dry run only' in out and 'omp-memory' in out and 'drop HERMES_HOME' in out


def test_apply_moves_inputs_into_agentmesh_and_rollback_restores(scratch):
    s = scratch
    hermes_before = {p: (s['hermes']/p).read_bytes() for p in ('cron/usage_audit.jsonl', 'cron/jobs.json', 'state.db')}
    assert deploy_insight.main(s['args']+['--apply']) == 0
    release = s['am']/'status'/('insight-release-'+s['rev'][:12])
    manifest = json.loads((release/'release.json').read_text())
    assert manifest['revision'] == s['rev'] and set(manifest['files']) == set(deploy_insight.FILES)
    assert oct(release.stat().st_mode & 0o777) == '0o700' and oct((release/'server.py').stat().st_mode & 0o777) == '0o600'
    # Hermes keeps its own files untouched; only omp-memory leaves.
    assert {p: (s['hermes']/p).read_bytes() for p in hermes_before} == hermes_before
    assert not (s['hermes']/'omp-memory').exists()
    trashed = list(s['trash'].glob('agentmesh-omp-memory-*'))
    assert len(trashed) == 1 and (trashed[0]/'summary-metrics.jsonl').exists()
    archive = s['am']/'backups/archive'
    assert 'stray' in (archive/deploy_insight.LEGACY_RUNS).read_text()
    rows = [json.loads(l) for l in (archive/'digest-cron/cron/usage_audit.jsonl').read_text().splitlines()]
    assert [r['fire_id'] for r in rows] == ['f1'] and rows[0]['error'] is True
    assert 'PRIVATE' not in (archive/'digest-cron/cron/usage_audit.jsonl').read_text()
    with sqlite3.connect(archive/'digest-cron/state.db') as c:
        assert c.execute('SELECT id FROM sessions').fetchall() == [('cron_%s_1' % deploy_insight.JOB,)]
        assert 'title' not in {r[1] for r in c.execute('PRAGMA table_info(sessions)')}
    value = plistlib.loads(s['plist'].read_bytes())
    assert value['ProgramArguments'] == ['/venv/python', '-u', str(release/'server.py'), '--port', '51877']
    assert value['WorkingDirectory'] == str(release)
    assert value['EnvironmentVariables'] == {'INSIGHT_PRIMARY_BACKEND':'sqlite'}
    # The new Insight reads both moved inputs from the AgentMesh home alone.
    data = adapters.metrics(s['am'])
    assert [r['run_id'] for r in data['runs']['bounded']] == ['stray']
    assert [r['run_id'] for r in data['runs']['historical']] == ['f1']
    assert data['report']['baseline_matched_sessions'] == 1
    # Re-running is idempotent.
    assert deploy_insight.main(s['args']+['--apply']) == 0
    assert len(list(s['trash'].glob('agentmesh-omp-memory-*'))) == 1

    old_rev = subprocess.run(['git', '-C', str(REPO), 'rev-parse', 'HEAD~1'], check=True, capture_output=True, text=True).stdout.strip()
    rollback = [a if a != s['rev'] else old_rev for a in s['args']]
    assert deploy_insight.main(rollback+['--rollback', '--apply']) == 0
    assert (s['hermes']/'omp-memory/summary-metrics.jsonl').exists()
    assert not list(s['trash'].glob('agentmesh-omp-memory-*'))
    value = plistlib.loads(s['plist'].read_bytes())
    assert value['EnvironmentVariables']['HERMES_HOME'] == str(s['hermes'])
    assert value['WorkingDirectory'].endswith('insight-release-'+old_rev[:12])
    assert value['ProgramArguments'][2].endswith('insight-release-'+old_rev[:12]+'/server.py') and len(value['ProgramArguments']) == 5


def test_refuses_unexpected_plist(scratch):
    value = plistlib.loads(scratch['plist'].read_bytes())
    value['ProgramArguments'][2] = '/elsewhere/other.py'
    scratch['plist'].write_bytes(plistlib.dumps(value))
    before = snapshot(scratch['am'].parent)
    with pytest.raises(SystemExit):
        deploy_insight.main(scratch['args']+['--apply'])
    assert snapshot(scratch['am'].parent) == before
