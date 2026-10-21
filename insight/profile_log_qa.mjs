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
const renderer=html.slice(html.indexOf('function renderProfileLog('),html.indexOf('function renderPeers('));
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
assert.ok(nodes.get('profile-log-note').textContent.includes('truncated'));
vm.runInNewContext(helpers+renderer+'renderProfileLog(fixture)',{document,fixture:{available:false,availability:'missing'}});
assert.ok(nodes.get('profile-log-note').textContent.includes('missing'));
assert.ok(nodes.get('profile-log-profiles').innerHTML.includes('No observed records'));
console.log('PASS: Profile log navigation, fixture rendering, escaping, truncation and missing snapshot');
