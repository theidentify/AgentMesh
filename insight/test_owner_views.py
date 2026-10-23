"""Failure reasons, identity overrides and previously seen devices; temporary fixtures only."""
import json
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

import adapters
from test_sync_view import HARNESS

SCRIPT = Path(__file__).with_name('index.html').read_text(encoding='utf-8').split('<script>')[1].split('</script>')[0]


def run(start, status='committed', detail=None, cost=0.01, **extra):
    return dict(record_type='run', run_id='r-'+start, status=status, start=start, end=start, error_detail=detail,
                errors=['PRIVATE prose'] if detail else [], actual_cost_usd=cost, **extra)


def metrics_home(tmp_path, rows):
    am = tmp_path/'agentmesh'
    (am/'jobs').mkdir(parents=True)
    (am/'jobs/summary-metrics.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    return tmp_path/'legacy', am


def test_failure_reasons_counts_cost_and_latest(tmp_path):
    quota = 'model worker failed: api_error 429; coverage unchanged'
    home, am = metrics_home(tmp_path, [
        run('2026-10-22T01:49:17+00:00', 'failed', 'stable key identity changed', 0.02),
        run('2026-10-22T03:47:03+00:00', 'failed', 'evidence coverage mismatch', None),
        run('2026-10-22T04:00:00+00:00', 'failed', 'stable key identity changed', 0.03),
        run('2026-10-23T03:39:42+00:00', 'failed', quota, 0),
        run('2026-10-23T06:00:00+00:00', 'failed', quota, 0),
        run('2026-10-23T06:10:00+00:00', 'failed', 'secret <script>'+'x'*300, 0.5),
        run('2026-10-23T06:12:00+00:00', 'failed', None, None),
        run('2026-10-23T06:20:00+00:00'),
    ])
    data = adapters.metrics(home, am)
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


def test_metrics_without_legacy_cron_home(tmp_path):
    """The ~/.agentmesh layout alone is enough: no Hermes cron files are required."""
    home, am = metrics_home(tmp_path, [run('2026-10-23T06:20:00+00:00', llm_api_calls=1)])
    data = adapters.metrics(home, am)
    assert [r['run_id'] for r in data['runs']['bounded']] == ['r-2026-10-23T06:20:00+00:00']
    assert data['runs']['historical'] == [] and data['report']['baseline']['sample_count'] == 0
    assert data['failure_reasons'] == {'total':0, 'latest_at':None, 'rows':[]}


def test_metrics_cli_defaults_to_agentmesh_layout(tmp_path, monkeypatch, capsys):
    import metrics
    _, am = metrics_home(tmp_path, [run('2026-10-23T06:20:00+00:00', llm_api_calls=1)])
    monkeypatch.delenv('HERMES_HOME', raising=False)
    monkeypatch.setenv('AGENTMESH_HOME', str(am))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path/'nohome')
    metrics.main(['--json'])
    assert json.loads(capsys.readouterr().out)['new']['sample_count'] == 1


def test_server_home_default_is_agentmesh(monkeypatch):
    import server
    monkeypatch.delenv('HERMES_HOME', raising=False)
    monkeypatch.setenv('AGENTMESH_HOME', '/fixture/agentmesh')
    assert server.legacy_home_default() == Path('/fixture/agentmesh')
    monkeypatch.setenv('HERMES_HOME', '/fixture/legacy')
    assert server.legacy_home_default() == Path('/fixture/legacy')


@pytest.fixture
def memory_db(tmp_path):
    db = tmp_path/'memory.db'
    with sqlite3.connect(db) as c:
        c.executescript((Path(__file__).parent.parent/'memory-sync/schema.sql').read_text())
    return db


def test_detail_shows_identity_from_existing_and_history(memory_db):
    metadata = {'identity_from_existing':['kind','scope_key','PRIVATE<b>'], 'batch_id':'b-3',
                'evidence_history':[{'evidence':[{'event_id':1,'quote':'PRIVATE quote'}], 'batch_id':'b-1'},
                                    {'evidence':[], 'batch_id':'b-2', 'identity_from_existing':['project']},
                                    'junk']}
    with sqlite3.connect(memory_db) as c:
        c.execute("INSERT INTO memory_items(kind,scope,project,content,metadata) VALUES ('fact','project','demo','x',?)", (json.dumps(metadata),))
        c.execute("INSERT INTO memory_items(kind,scope,project,content,metadata) VALUES ('fact','project','demo','y','{}')")
    reader = adapters.MemoryReader(sqlite=memory_db)
    value = reader.detail('items', 1)
    assert value['identity'] == {'identity_from_existing':['kind','scope_key'], 'batch_id':'b-3',
                                 'history':[{'revision':2, 'batch_id':'b-2', 'identity_from_existing':['project']}]}
    assert 'PRIVATE' not in json.dumps(value)
    assert reader.detail('items', 2)['identity'] == {'identity_from_existing':None, 'batch_id':None, 'history':[]}


def test_devices_seen_in_ingested_memory(memory_db):
    events = [('claude', '/Users/me/.claude/projects/a/1.jsonl', '2026-10-23T08:00:00Z'),
              ('codex', '/Users/me/.codex/sessions/2.jsonl', '2026-10-23T05:00:00Z'),
              ('claude', 'C:\\Users\\krisb\\.claude\\projects\\x\\3.jsonl', '2026-10-16T05:51:54Z'),
              ('claude', 'C:\\Users\\krisb\\.claude\\projects\\y\\4.jsonl', '2026-10-15T00:00:00Z'),
              ('omp', '', '2026-09-22T17:02:34Z')]
    with sqlite3.connect(memory_db) as c:
        for n, (agent, path, at) in enumerate(events):
            c.execute('INSERT INTO observation_events(event_key,source_agent,source_path,occurred_at,kind,content,source_hash) VALUES (?,?,?,?,?,?,?)',
                      ('k%d' % n, agent, path, at, 'user', 'PRIVATE', 'h'))
    reader = adapters.MemoryReader(sqlite=memory_db)
    now = datetime(2026, 10, 23, 9, tzinfo=timezone.utc)
    value = adapters.devices_seen(reader, now=now)
    rows = {r['platform']:r for r in value['rows']}
    assert set(rows) == {'macos', 'windows', 'unknown'}
    win = rows['windows']
    assert (win['events'], win['last_seen_at'], win['agents'], win['state']) == (2, '2026-10-16T05:51:54Z', ['claude'], 'stale')
    assert rows['macos']['agents'] == ['claude', 'codex'] and rows['macos']['state'] == 'recent'
    assert value['rows'][0]['platform'] == 'macos'
    text = json.dumps(value)
    assert 'krisb' not in text and '/Users/me' not in text and 'PRIVATE' not in text


def test_operations_include_devices_seen(memory_db, tmp_path):
    with sqlite3.connect(memory_db) as c:
        c.execute("INSERT INTO observation_events(event_key,source_agent,source_path,occurred_at,kind,content,source_hash) VALUES ('k','claude','C:\\x\\y','2026-10-16T05:51:54Z','user','x','h')")
    value = adapters.operations(tmp_path, adapters.MemoryReader(sqlite=memory_db), None, {'status':'unknown','rows':[]}, tmp_path/'am')
    assert [r['platform'] for r in value['devices_seen']['rows']] == ['windows']


DEVICE_HARNESS = HARNESS.split('const peer=')[0] + r'''
const old=new Date(Date.now()-7*86400e3).toISOString(), now=new Date().toISOString();
context.LOG={last_sync:{outcome:'ok',stale:false,age_seconds:20,outbox:0,local_objects:3,remote_objects:3},overview:{devices:[{device_id:'a'.repeat(32),name:'Mac',platform:'macos',this_device:true,revoked:false,last_seen_at:now,last_sync_at:now,last_sync_outcome:'ok'}]}};
context.SEEN={available:true,rows:[{platform:'macos',label:'macOS',events:9,agents:['claude'],last_seen_at:now,state:'recent'},{platform:'windows',label:'Windows <img>',events:736,agents:['claude'],last_seen_at:old,state:'stale'}]};
vm.runInContext('ops={sync:{peers:{rows:[]}},devices_seen:SEEN};renderProfileLog(LOG);profileLog=LOG;renderSyncSummary();',context);
const html=element('sync-profile').innerHTML, head=element('sync-headline').innerHTML;
assert.match(html,/Windows &lt;img&gt;/);assert.ok(!html.includes('<img'));
assert.match(html,/not in the profile log/);
assert.match(html,/last activity 7d ago/);
assert.match(head,/Windows &lt;img&gt; last sent memory 7d ago and has not joined the profile log/);
assert.ok(!/macOS last sent/.test(head));
assert.match(element('profile-log-devices').innerHTML,/Windows &lt;img&gt;/);
'''


def test_previously_seen_device_is_listed_as_stale():
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
