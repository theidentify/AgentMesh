import json
from pathlib import Path
import subprocess
from test_sync_view import HARNESS


def test_st7_view():
    script=Path(__file__).with_name('index.html').read_text().split('<script>')[1].split('</script>')[0]
    harness=HARNESS.split('const peer=')[0]+r'''
vm.runInContext("renderStorage({available:false,pending:true})",context);
assert.match(element('storage-note').textContent,/Computing/);
context.DATA={available:true,computed_at:'2026-10-22T12:00:00Z',sqlite:{dbstat:false,rows:{memory_items:3},tables:[{name:'<img src=x onerror=alert(1)>',rows:3,retired:true,note:'removed in 7b'}]},disk:{categories:[{category:'<script>bad</script>',bytes:9}],reclaimable:[]},costs:{summary:{month:null},r2:{total:null},pricing:{prices:{as_of:'2026-10-22',sources:[],cloudflare:{}}}},compute:{services:[{service:'broker',state:'not running',cpu_s:null}],jobs:[]},buffers:[],network:{}};
vm.runInContext('renderStorage(DATA)',context);
const all=[...elements.values()].map(e=>e.innerHTML).join('');
assert.match(all,/dbstat unavailable/);assert.match(all,/memory_items/);assert.match(all,/removed in 7b/);
assert.match(all,/&lt;img/);assert.match(all,/&lt;script&gt;/);assert(!all.includes('<img'));
assert.match(all,/API-equivalent cost/);assert.match(all,/unknown/);assert.match(all,/not running/);
assert.match(all,/Insight never deletes anything/);
assert.match(element('storage-note').textContent,/refreshes every 10 minutes/);
vm.runInContext("remote=true;navigate('storage')",context);assert.equal(context.location.hash,'storage');
'''
    result=subprocess.run(['node','-e','const SCRIPT='+json.dumps(script)+';'+harness],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_storage_pending_polling():
    script=Path(__file__).with_name('index.html').read_text().split('<script>')[1].split('</script>')[0]
    harness=HARNESS.split('const peer=')[0]+r'''
const timers=new Map();let nextTimer=0,calls=0,reply={available:false,pending:true};
context.setTimeout=(fn,ms)=>{assert.equal(ms,2000);timers.set(++nextTimer,fn);return nextTimer};
context.clearTimeout=id=>timers.delete(id);
context.fetch=async url=>{assert.equal(url,'/api/storage');calls++;return {ok:true,json:async()=>reply}};
async function refresh(){await vm.runInContext('refreshStorage()',context)}
async function tick(){assert.equal(timers.size,1);const [id,fn]=[...timers][0];timers.delete(id);fn();await new Promise(r=>setImmediate(r));}
(async()=>{
  vm.runInContext("remote=false;view='storage'",context);
  await Promise.all([refresh(),refresh()]);
  assert.equal(calls,1);assert.equal(timers.size,1);
  assert.equal(element('storage-note').textContent,'Computing… (first load)');
  await tick();assert.equal(calls,2);assert.equal(timers.size,1);
  await refresh();assert.equal(timers.size,1);
  reply={available:true};await tick();assert.equal(timers.size,0);
  reply={available:false,pending:true};await refresh();assert.equal(timers.size,1);
  vm.runInContext("navigate('overview')",context);assert.equal(timers.size,0);
  const before=calls;await refresh();assert.equal(calls,before);
  let resolve;
  context.fetch=()=>new Promise(r=>{resolve=r});
  vm.runInContext("navigate('storage');navigate('overview')",context);
  resolve({ok:true,json:async()=>reply});
  await new Promise(r=>setImmediate(r));assert.equal(timers.size,0);
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    result=subprocess.run(['node','-e','const SCRIPT='+json.dumps(script)+';'+harness],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
