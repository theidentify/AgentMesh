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
