import json
import shutil
import subprocess
from pathlib import Path

import pytest


HARNESS = r'''
const vm=require('node:vm'), assert=require('node:assert/strict');
const elements=new Map();
function element(id){if(!elements.has(id))elements.set(id,{id,innerHTML:'',className:'',textContent:'',classList:{toggle(){}},setAttribute(){}});return elements.get(id)}
const context={console,Intl,Date,Object,String,Number,URLSearchParams,location:{hash:''},localStorage:{getItem(){},setItem(){}},setInterval(){},fetch:()=>new Promise(()=>{}),document:{getElementById:element,body:element('body'),addEventListener(){},querySelector:element,querySelectorAll(){return []}},window:{scrollTo(){},addEventListener(){}}};
vm.createContext(context);
vm.runInContext(SCRIPT,context);
const peer=(name,fresh,age)=>({peer:name,ack_at:'2026-10-22T09:00:00Z',age_seconds:age,freshness:fresh,has_error:false,sync:{received:5,pending:0,conflict:0,invalid:0},counts:{memory_items:10},cycle:{published:1,applied:0}});
function run(sync,log){context.SYNC=sync;context.LOG=log;vm.runInContext('ops={sync:SYNC};profileLog=LOG;renderSyncSummary();',context);return [element('sync-headline'),element('sync-devices').innerHTML]}
let [head,devices]=run({node:'mac',peers:{rows:[peer('Mac','fresh',30),peer('Windows','fresh',40)]},transport_status:{rows:[{peer:'Windows',status:'connected'}]}},{last_sync:{outcome:'ok',stale:false,age_seconds:20,outbox:0,local_objects:3,remote_objects:3}});
assert.equal(head.className,'sync-headline');
assert.match(head.innerHTML,/Everything is in sync/);
assert.match(devices,/this device/);
assert.match(devices,/Profile log → R2/);
[head,devices]=run({node:'mac',peers:{rows:[peer('Mac','fresh',30),peer('Windows','stale',5*86400)]},transport_status:{rows:[{peer:'Windows',status:'disconnected'}]}},{last_sync:{outcome:'remote_error',last_ok_at:'x',last_ok_age_seconds:7200}});
assert.equal(head.className,'sync-headline bad');
assert.match(head.innerHTML,/3 things need attention/);
assert.match(head.innerHTML,/Windows last finished a sync cycle 5d ago/);
assert.match(head.innerHTML,/Syncthing cannot reach Windows \(disconnected\)/);
assert.match(head.innerHTML,/remote error; last success 2h ago/);
assert.match(devices,/Last synced 5d ago/);
[head]=run({node:'mac',peers:{rows:[{...peer('Mac','fresh',30),sync:{pending:2,conflict:1,invalid:0}}]}},{last_sync:null});
assert.equal(head.className,'sync-headline warn');
assert.match(head.innerHTML,/Mac has 3 packets waiting/);
'''


def test_sync_summary_plain_language():
    """The Sync view leads with one readable verdict per device."""
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable for UI script check')
    script = Path(__file__).with_name('index.html').read_text(encoding='utf-8').split('<script>')[1].split('</script>')[0]
    result = subprocess.run([node, '-e', 'const SCRIPT='+json.dumps(script)+';'+HARNESS], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr


def test_launchd_jobs_replace_retired_cron(tmp_path):
    """Pipeline and Metrics read the launchd job wrapper status, not the retired Hermes cron."""
    import plistlib
    import adapters
    (tmp_path/'status').mkdir()
    agents = tmp_path/'LaunchAgents'
    agents.mkdir()
    (tmp_path/'status/job-ingest.json').write_text(json.dumps({'ok':True,'at':'2026-10-22T09:00:00+00:00','since':'2026-10-22T09:00:00+00:00'}))
    (tmp_path/'status/job-summary.json').write_text(json.dumps({'ok':False,'at':'2026-10-22T09:05:00+00:00','since':'2026-10-22T08:00:00+00:00'}))
    with open(agents/'org.agentmesh.summary.plist', 'wb') as f:
        plistlib.dump({'Label':'org.agentmesh.summary','StartInterval':300}, f)
    ingest, summary = adapters.launchd_jobs(tmp_path, agents)
    assert (ingest['last_status'], ingest['enabled'], ingest['next_run_at']) == ('ok', False, None)
    assert (summary['last_status'], summary['has_error'], summary['failing_since']) == ('error', True, '2026-10-22T08:00:00+00:00')
    assert (summary['state'], summary['next_run_at']) == ('every 5 min', '2026-10-22T09:10:00+00:00')
    (tmp_path/'status/job-ingest.json').write_text('[]')
    assert [row['name'] for row in adapters.launchd_jobs(tmp_path, agents)] == ['Summary']


def test_job_failures_from_wrapper_log(tmp_path):
    """Whole-run failures come from the job wrapper log; unsafe text never passes through."""
    from datetime import datetime, timezone
    import adapters
    (tmp_path/'status').mkdir()
    now = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
    lines = [{'at':'2026-10-22T11:00:00+00:00','exit':0,'job':'ingest'},
             {'at':'2026-10-22T11:30:00+00:00','exit':1,'job':'ingest','error_class':'ValueError','status':'blocked'},
             {'at':'2026-10-18T11:30:00+00:00','exit':1,'job':'ingest','error_class':'OSError'},
             {'at':'2026-10-01T11:30:00+00:00','exit':1,'job':'ingest','error_class':'Old'},
             {'at':'2026-10-22T11:40:00+00:00','exit':1,'job':'ingest','error_class':'<b>x','detail':'secret\nline'}]
    (tmp_path/'status/launchd-ingest.log').write_text('skipped partial line\n'+'\n'.join(json.dumps(l) for l in lines)+'\nnot json\n')
    data = adapters.job_failures(tmp_path, now)
    assert data['counts'] == {'ingest':{'day':3,'day_failed':2,'week_failed':3}}
    assert [row['error_class'] for row in data['rows']] == [None, 'ValueError', 'OSError']
    assert data['rows'][0]['detail'] is None and data['rows'][1]['status'] == 'blocked'
    assert adapters.job_failures(tmp_path/'missing', now) == {'available':False,'counts':{},'rows':[]}


def test_overview_verdict_lists_job_failures():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable for UI script check')
    script = Path(__file__).with_name('index.html').read_text(encoding='utf-8').split('<script>')[1].split('</script>')[0]
    harness = HARNESS.split("const peer=")[0] + r'''
const recent=new Date(Date.now()-3600e3).toISOString();
context.DATA={pipeline:{jobs:[{name:'Ingest',has_error:false},{name:'Summary',has_error:true}],failures:{counts:{ingest:{day:60,day_failed:2,week_failed:3},summary:{day:10,day_failed:4,week_failed:4}},rows:[{job:'Summary',at:recent,exit:1,error_class:'ValidationError',status:'blocked',detail:'model worker unavailable'},{job:'Ingest',at:recent,exit:1,error_class:'ValueError',status:'blocked'}]}},agents:{rows:[]}};
vm.runInContext("ops=DATA;connections={rows:[{id:'sqlite',label:'SQLite',available:true,items:5,summaries:2}]};syncProblems=['Windows last finished a sync cycle 5d ago.'];renderOverview();",context);
const head=element('overview-headline').innerHTML;
assert.equal(element('overview-headline').className,'sync-headline bad');
assert.match(head,/3 things need attention/);
assert.match(head,/Summary job is failing right now: ValidationError · model worker unavailable/);
assert.match(head,/Ingest job failed 2 times in the last 24 h/);
assert.match(element('overview-cards').innerHTML,/6 failed runs in the last 24 h/);
assert.match(element('overview-failures').innerHTML,/ValueError/);
'''
    result = subprocess.run([node, '-e', 'const SCRIPT='+json.dumps(script)+';'+harness], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr
