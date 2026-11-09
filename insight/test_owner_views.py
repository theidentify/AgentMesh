"""Failure reasons, identity overrides and previously seen devices; temporary fixtures only."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import adapters
from test_sync_view import SETUP

SCRIPT = Path(__file__).with_name('index.html').read_text(encoding='utf-8').split('<script>')[1].split('</script>')[0]


def run(start, status='committed', detail=None, cost=0.01, **extra):
    return dict(record_type='run', run_id='r-'+start, status=status, start=start, end=start, error_detail=detail,
                errors=['PRIVATE prose'] if detail else [], actual_cost_usd=cost, **extra)


def metrics_home(tmp_path, rows):
    am = tmp_path/'agentmesh'
    (am/'jobs').mkdir(parents=True)
    (am/'jobs/summary-metrics.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    return am


def test_failure_reasons_counts_cost_and_latest(tmp_path):
    quota = 'model worker failed: api_error 429; coverage unchanged'
    am = metrics_home(tmp_path, [
        run('2026-10-22T01:49:17+00:00', 'failed', 'stable key identity changed', 0.02),
        run('2026-10-22T03:47:03+00:00', 'failed', 'evidence coverage mismatch', None),
        run('2026-10-22T04:00:00+00:00', 'failed', 'stable key identity changed', 0.03),
        run('2026-10-23T03:39:42+00:00', 'failed', quota, 0),
        run('2026-10-23T06:00:00+00:00', 'failed', quota, 0),
        run('2026-10-23T06:10:00+00:00', 'failed', 'secret <script>'+'x'*300, 0.5),
        run('2026-10-23T06:12:00+00:00', 'failed', None, None),
        run('2026-10-23T06:20:00+00:00'),
    ])
    data = adapters.metrics(am)
    rows = {r['reason']:r for r in data['failure_reasons']['rows']}
    assert set(rows) == {'stable key identity changed', 'evidence coverage mismatch', quota, 'other (unrecognised detail)', 'no detail recorded'}
    stable = rows['stable key identity changed']
    assert (stable['count'], stable['first_at'], stable['latest_at']) == (2, '2026-10-22T01:49:17+00:00', '2026-10-22T04:00:00+00:00')
    assert stable['cost_usd'] == pytest.approx(0.05) and stable['cost_known'] == 2
    assert rows['evidence coverage mismatch']['cost_usd'] is None and rows['evidence coverage mismatch']['cost_known'] == 0
    assert rows[quota]['quota'] is True and rows[quota]['count'] == 2 and not stable['quota']
    # Newest failure first so a reason that stopped sinks below current ones.
    assert data['failure_reasons']['rows'][0]['reason'] == 'no detail recorded'
    assert data['failure_reasons']['total'] == 7
    assert data['failure_reasons']['latest_at'] == '2026-10-23T06:12:00+00:00'
    assert [r['error_detail'] for r in data['runs']['bounded']][:2] == ['stable key identity changed', 'evidence coverage mismatch']
    text = json.dumps(data)
    assert 'PRIVATE' not in text and '<script>' not in text


def test_metrics_without_cron_archive(tmp_path):
    """The ~/.agentmesh layout alone is enough: the frozen cron archive is optional."""
    am = metrics_home(tmp_path, [run('2026-10-23T06:20:00+00:00', llm_api_calls=1)])
    data = adapters.metrics(am)
    assert [r['run_id'] for r in data['runs']['bounded']] == ['r-2026-10-23T06:20:00+00:00']
    assert data['runs']['historical'] == [] and data['report']['baseline']['sample_count'] == 0
    assert data['failure_reasons'] == {'total':0, 'latest_at':None, 'rows':[]}


def test_metrics_cli_defaults_to_agentmesh_layout(tmp_path, monkeypatch, capsys):
    import metrics
    am = metrics_home(tmp_path, [run('2026-10-23T06:20:00+00:00', llm_api_calls=1)])
    monkeypatch.delenv('HERMES_HOME', raising=False)
    monkeypatch.setenv('AGENTMESH_HOME', str(am))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path/'nohome')
    metrics.main(['--json'])
    assert json.loads(capsys.readouterr().out)['new']['sample_count'] == 1


def test_cron_archive_lives_in_agentmesh_home(tmp_path, monkeypatch):
    """The retired cron's history is read from the AgentMesh archive, never a Hermes home."""
    am = metrics_home(tmp_path, [])
    archive = am/'backups/archive/digest-cron'
    (archive/'cron').mkdir(parents=True)
    (archive/'cron/usage_audit.jsonl').write_text(json.dumps({'job_id':adapters.JOB,'fire_id':'f1','ts':'2026-10-01T00:00:00Z','prompt_tokens':5,'completion_tokens':1,'duration_ms':1000})+'\n')
    monkeypatch.setenv('HERMES_HOME', str(tmp_path/'hermes'))
    assert adapters.cron_archive(am) == archive
    data = adapters.metrics(am)
    assert [r['run_id'] for r in data['runs']['historical']] == ['f1']
    assert data['report']['baseline']['input_total_tokens']['total'] == 5
    assert 'hermes_sessions' not in adapters.operations(am)


DEVICE_HARNESS = SETUP + r'''
const now=new Date().toISOString();
context.LOG={last_sync:{outcome:'ok',stale:false,age_seconds:20,outbox:0,local_objects:3,remote_objects:3},overview:{devices:[{device_id:'a'.repeat(32),name:'Mac <img>',platform:'macos',this_device:true,revoked:false,last_seen_at:now,last_sync_at:now,last_sync_outcome:'ok'}]}};
vm.runInContext('ops={};renderProfileLog(LOG);profileLog=LOG;renderSyncSummary();',context);
const html=element('sync-profile').innerHTML;
assert.match(html,/Mac &lt;img&gt;/);assert.ok(!html.includes('<img'));
assert.match(html,/1 device/);assert.match(html,/this device/);
assert.ok(!/not in the profile log/.test(html+element('profile-log-devices').innerHTML));
'''


def test_profile_log_devices_are_listed():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable for UI script check')
    result = subprocess.run([node, '-e', 'const SCRIPT='+json.dumps(SCRIPT)+';'+DEVICE_HARNESS], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr


METRICS_HARNESS = r'''
const vm=require('node:vm'), assert=require('node:assert/strict');
const elements=new Map();
function element(id){if(!elements.has(id))elements.set(id,{id,innerHTML:'',textContent:'',value:'all',addEventListener(){},setAttribute(){}});return elements.get(id)}
const context={Intl,Date,Object,String,Number,Math,JSON,parent:null,localStorage:{getItem(){},setItem(){}},setInterval(){},fetch:()=>new Promise(()=>{}),document:{getElementById:element,body:element('body'),querySelectorAll(){return []}},location:{origin:'x'},addEventListener(){}};
context.window=context;
vm.createContext(context);
vm.runInContext(SCRIPT,context);
const old=new Date(Date.now()-2*86400e3).toISOString();
context.DATA={failure_reasons:{total:3,latest_at:old,rows:[{reason:'model worker failed: api_error 429 <b>',count:2,last_24h:0,cost_usd:0,cost_known:2,first_at:old,latest_at:old,quota:true},{reason:'stable key identity changed',count:1,last_24h:0,cost_usd:null,cost_known:0,first_at:old,latest_at:old,quota:false}]}};
vm.runInContext('data=DATA;renderFailures()',context);
const html=element('failures').innerHTML;
assert.match(html,/pill error">quota/);assert.match(html,/api_error 429 &lt;b&gt;/);assert.ok(!html.includes('<b>'));
assert.match(html,/stable key identity changed/);assert.match(html,/2 days ago/);assert.match(html,/unknown/);
assert.match(element('failureTotal').textContent,/3 failed runs/);
'''


def test_metrics_page_lists_failure_reasons():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable for UI script check')
    script = Path(__file__).with_name('metrics.html').read_text(encoding='utf-8').split('<script>')[1].split('</script>')[0]
    result = subprocess.run([node, '-e', 'const SCRIPT='+json.dumps(script)+';'+METRICS_HARNESS], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr
