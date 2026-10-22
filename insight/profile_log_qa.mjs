// Fixture-only rendering QA: execute the actual helpers and Profile log renderer.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const html=fs.readFileSync(new URL('./index.html',import.meta.url),'utf8');
assert.ok(html.includes('data-view="profile-log"'));
assert.ok(html.includes('<section id="profile-log" hidden>'));
const section=html.split('<section id="profile-log"')[1].split('</section>')[0];
assert.ok(!/<form|<input|<button/.test(section));
assert.ok(section.includes('agentmesh-log admin / agentmesh-log apply'));
assert.ok(html.includes("jsonFetch('/api/profile-log')"));
const helpers=html.slice(html.indexOf('const $='),html.indexOf('const agentColumns='));
const renderer=html.slice(html.indexOf('function deviceTable('),html.indexOf('function renderPeers('));
const nodes=new Map();
const document={getElementById(id){if(!nodes.has(id))nodes.set(id,{innerHTML:'',textContent:''});return nodes.get(id)}};
const attack='<img src=x onerror="throw 1">&\'';
const fixture={available:true,truncated:true,age_seconds:3,updated_at:'2026-10-20T00:00:00Z',
 overview:{profiles:[{name:attack,scopes:[{path:attack,label:attack}]}],agents:[{name:attack,read:[attack],write:[attack],default_write:attack,allow_secret:attack}],devices:[{device_id:attack,revoked:false},{device_id:'revoked',revoked:true}]},
 migration:{unmapped:[attack],projects:[{project:attack,items:3,summaries:1,target:attack}]},
 last_apply:{outcome:attack},last_catch_up:{outcome:attack,outcomes:{[attack]:2},skipped:{[attack]:1},errors:[attack]}};
vm.runInNewContext(helpers+renderer+'renderProfileLog(fixture)',{document,fixture});
for(const id of ['cards','profiles','agents','devices','migration','outcomes']){
 const rendered=nodes.get('profile-log-'+id).innerHTML;
 assert.ok(rendered.includes('&lt;img'),id+' must render escaped fixture values');
 assert.ok(!rendered.includes('<img'),id+' must never interpolate raw markup');
}
assert.ok(nodes.get('profile-log-devices').innerHTML.includes('active'));
assert.ok(nodes.get('profile-log-devices').innerHTML.includes('revoked'));
assert.ok(nodes.get('profile-log-note').textContent.includes('cut to the first 500'));
vm.runInNewContext(helpers+renderer+'renderProfileLog(fixture)',{document,fixture:{available:false,availability:'missing'}});
assert.ok(nodes.get('profile-log-note').textContent.includes('missing'));
assert.ok(nodes.get('profile-log-profiles').innerHTML.includes('Shown after the next agentmesh-log apply'));
console.log('PASS: Profile log navigation, fixture rendering, escaping, truncation and missing snapshot');

assert.ok(section.includes('broker sync loop'));
const renderSync=last_sync=>{vm.runInNewContext(helpers+renderer+'renderProfileLog(fixture)',{document,fixture:{available:true,last_sync}});return nodes.get('profile-log-sync').innerHTML};
assert.ok(renderSync(undefined).includes('Off — not configured'));
const sync={outcome:'ok',at:'2026-10-20T00:00:00Z',age_seconds:60,outbox:0,local_objects:4,remote_objects:5,stale:false};
assert.ok(renderSync(sync).includes('Last sync 1m ago · 0 waiting to upload · 4 objects here, 5 in R2'));
assert.ok(!renderSync(sync).includes('>stale<'));
for(const outcome of ['remote_error','credentials_missing','credentials_refused','error',attack]){
 const rendered=renderSync({...sync,outcome});
 assert.ok(rendered.includes(outcome==='remote_error'?'sync-warning':'sync-error'));
 assert.ok(rendered.includes('last ok never')); assert.ok(!rendered.includes('<img'));
 if(outcome===attack)assert.ok(rendered.includes('&lt;img'));
}
assert.ok(renderSync({...sync,stale:true}).includes('>stale<'));
assert.ok(renderSync({...sync,outcome:'remote_error',last_ok_at:sync.at,last_ok_age_seconds:120}).includes('last ok 2m ago'));
assert.ok(!/<form|<input|<button/.test(renderSync(sync)));
console.log('PASS: Remote sync absent, ok, errors, stale and escaping');
