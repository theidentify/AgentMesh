// Real-browser QA using Node's native WebSocket. No npm dependencies.
// node insight/qa.mjs URL DEVTOOLS_PORT OUTPUT_DIRECTORY
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
const [url, port, output] = process.argv.slice(2);
assert.ok(url && port && output, 'Pass URL, isolated Chromium debug port and external output directory');
const repo=path.resolve(fileURLToPath(new URL('../',import.meta.url)));
assert.ok(path.resolve(output)!==repo&&!path.resolve(output).startsWith(repo+path.sep),'QA artifacts must remain outside Git');
fs.mkdirSync(output, {recursive:true});
const target=(await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(t=>t.type==='page');
const ws=new WebSocket(target.webSocketDebuggerUrl);
await new Promise(resolve=>ws.addEventListener('open',resolve,{once:true}));
let next=0;
const pending=new Map(), errors=[];
ws.addEventListener('message',event=>{const data=JSON.parse(event.data);if(data.id){const p=pending.get(data.id);pending.delete(data.id);data.error?p.reject(data.error):p.resolve(data.result)}else if(data.method==='Runtime.exceptionThrown')errors.push(data.params.exceptionDetails)});
function send(method,params={}){return new Promise((resolve,reject)=>{const id=++next;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}))})}
async function evaluate(expression){const result=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(result.exceptionDetails)throw Error(JSON.stringify(result.exceptionDetails));return result.result.value}
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(expression){for(let n=0;n<80;n++){if(await evaluate(expression))return;await delay(100)}throw Error('Timed out: '+expression)}
async function shot(name){const result=await send('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(output,name),Buffer.from(result.data,'base64'))}
await send('Runtime.enable');await send('Page.enable');
await send('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false});
await send('Page.navigate',{url});
await until("document.getElementById('updated')?.textContent.startsWith('Observed')");
await evaluate("if(document.body.classList.contains('dark'))document.getElementById('theme').click();window.scrollTo(0,0)");
await until("document.getElementById('metrics-frame').contentDocument.getElementById('notice')?.textContent.startsWith('Updated')");
assert.equal(await evaluate("document.querySelectorAll('nav button').length"),6);
const initial=await evaluate("document.getElementById('updated').textContent");
const initialMetrics=await evaluate("document.getElementById('metrics-frame').contentDocument.getElementById('notice').textContent");
assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth'),true);
await shot('insight-desktop-light.png');
const views=['overview','metrics','memory','agents','pipeline','sync'];
for(const name of views){await evaluate(`document.querySelector('nav [data-view="${name}"]').click()`);await delay(150);assert.equal(await evaluate(`document.getElementById('${name}').hidden`),false)}
await evaluate("document.querySelector('nav [data-view=sync]').click()");
await until("document.querySelectorAll('#peer-table tbody tr').length===2");
assert.equal(await evaluate("[...document.querySelectorAll('nav svg.icon')].every(s=>s.getAttribute('aria-hidden')==='true'&&s.getAttribute('viewBox')==='0 0 24 24') && document.querySelectorAll('nav svg.icon').length===6"),true);
const peerObserved=await evaluate("ops.sync.peers");
assert.deepEqual(await evaluate("[...document.querySelectorAll('#peer-table tbody tr')].map(r=>r.cells[0].textContent)"),['Mac','Windows']);
assert.ok(await evaluate("document.getElementById('peer-note').textContent.includes(Intl.DateTimeFormat().resolvedOptions().timeZone)"));
await evaluate("(()=>{const fixture=structuredClone(ops.sync.peers);fixture.rows[0]={...fixture.rows[0],available:false,availability:'missing',ack_at:null,age_seconds:null,freshness:'unknown',has_error:null};fixture.rows[1]={...fixture.rows[1],age_seconds:3600,freshness:'stale',has_error:true};renderPeers(fixture)})()");
assert.ok(await evaluate("document.getElementById('peer-table').textContent.includes('missing')&&document.getElementById('peer-table').textContent.includes('stale')&&document.getElementById('peer-table').textContent.includes('reported errors')"));
await evaluate("renderPeers(ops.sync.peers)");
for(const peer of peerObserved.rows){
  const cells=await evaluate(`Array.from(document.querySelectorAll('#peer-table tbody tr')).find(r=>r.cells[0].textContent===${JSON.stringify(peer.peer)}).cells`+'.length');
  assert.equal(cells,11);
  assert.equal(await evaluate(`Array.from(document.querySelectorAll('#peer-table tbody tr')).find(r=>r.cells[0].textContent===${JSON.stringify(peer.peer)}).cells[2].textContent`),await evaluate(`stamp(${JSON.stringify(peer.ack_at)})`));
  assert.equal(await evaluate(`Array.from(document.querySelectorAll('#peer-table tbody tr')).find(r=>r.cells[0].textContent===${JSON.stringify(peer.peer)}).cells[6].textContent`),await evaluate(`num(${JSON.stringify(peer.sync.received)})`));
}
for(const theme of ['light','dark']){
  if(await evaluate("document.body.classList.contains('dark')")!==(theme==='dark'))await evaluate("document.getElementById('theme').click()");
  await evaluate('window.scrollTo(0,0)');await delay(100);await shot(`insight-desktop-sync-${theme}.png`);
}
await evaluate("if(document.body.classList.contains('dark'))document.getElementById('theme').click()");
await evaluate("document.querySelector('nav [data-view=memory]').click()");
await until("document.querySelector('#memory-results [data-detail]')!==null");
assert.equal(await evaluate("document.querySelectorAll('#memory-results tbody tr').length"),25);
assert.ok(await evaluate("document.querySelector('#memory-results [data-detail]').offsetHeight<=44"),'Detail buttons must remain on one line');
await evaluate("document.getElementById('next').click()");
await until("document.getElementById('page-info').textContent.startsWith('26')");
await evaluate("document.getElementById('previous').click()");
await until("document.getElementById('page-info').textContent.startsWith('1–')");
await evaluate("document.getElementById('query').value='NO_SUCH_MEMORY_qa_9087';document.getElementById('memory-filters').requestSubmit()");
await until("document.getElementById('page-info').textContent==='0 matching records'");
await evaluate("document.getElementById('clear').click()");
await until("document.querySelector('#memory-results [data-detail]')!==null");
assert.equal(await evaluate("document.getElementById('query').value"),'');
await evaluate("(async()=>{const sample=(await(await fetch('/api/memory?limit=1')).json()).rows[0];window.qaFilter={kind:sample.kind,scope:sample.scope,status:sample.status};$('kind').value=sample.kind;$('scope').value=sample.scope;$('memory-status').value=sample.status;$('project').value=sample.project||'';$('memory-filters').requestSubmit()})()");
await until("document.querySelector('#memory-results [data-detail]')!==null");
assert.equal(await evaluate("[...document.querySelectorAll('#memory-results tbody tr')].every(r=>r.children[1].textContent===qaFilter.kind&&r.children[2].textContent===qaFilter.scope&&r.children[4].textContent===qaFilter.status)"),true);
await evaluate("document.getElementById('clear').click()");
await until("document.querySelector('#memory-results [data-detail]')!==null");
await evaluate("document.querySelector('#memory-results [data-detail]').click()");
await until("document.getElementById('detail-content').textContent.includes('Source evidence')");
assert.equal(await evaluate("document.getElementById('detail').hidden"),false);
await evaluate("document.getElementById('close-detail').click()");
assert.equal(await evaluate("document.getElementById('detail').hidden"),true);
await evaluate("document.getElementById('collection').value='summaries';document.getElementById('collection').dispatchEvent(new Event('change'))");
await until("document.querySelector('#memory-results [data-detail]')!==null");
assert.equal(await evaluate("document.getElementById('kind-label').hidden"),true);
await evaluate("document.getElementById('project').value='NO_SUCH_PROJECT_qa_9087';document.getElementById('memory-filters').requestSubmit()");
await until("document.getElementById('page-info').textContent==='0 matching records'");
await evaluate("document.getElementById('clear').click()");
await until("document.querySelector('#memory-results [data-detail]')!==null");
await evaluate("document.getElementById('connection').value='sqlite';document.getElementById('connection').dispatchEvent(new Event('change'))");
await until("document.getElementById('backend-label').textContent===connections.rows.find(r=>r.id==='sqlite').label && document.querySelector('#memory-results [data-detail]')!==null");
await evaluate("document.getElementById('connection').value='postgres';document.getElementById('connection').dispatchEvent(new Event('change'))");
await until("document.getElementById('backend-label').textContent===connections.rows.find(r=>r.id==='postgres').label && document.querySelector('#memory-results [data-detail]')!==null");
await evaluate("document.getElementById('connection').value='sqlite';document.getElementById('connection').dispatchEvent(new Event('change'))");
await until("document.getElementById('backend-label').textContent===connections.rows.find(r=>r.id==='sqlite').label && document.querySelector('#memory-results [data-detail]')!==null");
assert.ok(await evaluate("document.querySelector('#memory-results [data-detail]').offsetHeight<=44"),'Staged IDs must not squeeze detail buttons');
await evaluate("document.querySelector('nav [data-view=metrics]').click()");
const frame="document.getElementById('metrics-frame').contentDocument";
assert.ok(await evaluate(`${frame}.querySelectorAll('#tokens svg rect').length>0`));
for(const source of ['isolated','historical','bounded']){await evaluate(`${frame}.querySelector('[data-source=${source}]').click()`);assert.ok(await evaluate(`${frame}.getElementById('rowCount').textContent.includes('matching runs')`))}
for(const id of ['status','trigger','model']){await evaluate(`(()=>{const select=${frame}.getElementById('${id}');if(select.options.length>1){select.selectedIndex=1;select.dispatchEvent(new Event('change'))}})()`)}
await evaluate(`${frame}.getElementById('time').value='24';${frame}.getElementById('time').dispatchEvent(new Event('change'))`);
for(const id of ['status','trigger','model','time'])await evaluate(`${frame}.getElementById('${id}').value='all';${frame}.getElementById('${id}').dispatchEvent(new Event('change'))`);
await evaluate("document.getElementById('theme').click()");
await delay(100);
assert.equal(await evaluate(`${frame}.body.classList.contains('dark')`),true);
await evaluate("document.querySelector('nav [data-view=memory]').click()");await delay(150);
await shot('insight-desktop-memory-dark.png');
await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
for(const theme of ['dark','light']){if(await evaluate("document.body.classList.contains('dark')")!==(theme==='dark'))await evaluate("document.getElementById('theme').click()");for(const name of views){await evaluate(`document.querySelector('nav [data-view=${name}]').click()`);await delay(120);assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth'),true, `${theme} mobile ${name} overflow`)}await evaluate("document.querySelector('nav [data-view=overview]').click()");await evaluate('window.scrollTo(0,0)');await shot(`insight-mobile-${theme}.png`);await evaluate("document.querySelector('nav [data-view=sync]').click();window.scrollTo(0,0)");await shot(`insight-mobile-sync-${theme}.png`)}
await delay(22000);
const after=await evaluate("document.getElementById('updated').textContent");
assert.notEqual(initial,after);
assert.notEqual(initialMetrics,await evaluate("document.getElementById('metrics-frame').contentDocument.getElementById('notice').textContent"));
await evaluate("document.getElementById('refresh').click()");
await until("!document.getElementById('refresh').disabled");
assert.equal(errors.length,0,JSON.stringify(errors));
const report={verified:true,views,desktop:'1440x1100',mobile:'390x844',themes:['light','dark'],memory:'search, clear, project/kind/scope/status filters, collection, pagination, detail, connection isolation',metrics:'charts, three sources, time/status/trigger/model filters, refresh',documentOverflow:false,autoRefresh:{initial,after},runtimeExceptions:errors.length,peers:peerObserved,peerFixtures:'missing, stale and reported-error rendering',sidebar:'six local inline stroke SVG icons'};
fs.writeFileSync(path.join(output,'qa-result.json'),JSON.stringify(report,null,2));
console.log(JSON.stringify(report,null,2));ws.close();
