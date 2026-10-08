// Bounded real Chromium QA for read-only packet security, no npm dependencies.
// node insight/security_qa.mjs URL DEVTOOLS_PORT OUTPUT_DIRECTORY [legacy]
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
const [url, port, output, expectedPolicy] = process.argv.slice(2);
assert.ok(url && port && output, 'URL, isolated debug port and external evidence directory required');
const repo = path.resolve(fileURLToPath(new URL('../', import.meta.url)));
assert.ok(path.resolve(output)!==repo&&!path.resolve(output).startsWith(repo+path.sep), 'Evidence must remain outside Git');
fs.mkdirSync(output, {recursive:true});
const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
const target = targets.find(t=>t.type==='page');
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise(resolve=>ws.addEventListener('open',resolve,{once:true}));
let next = 0; const pending = new Map(), errors = [];
ws.addEventListener('message', event=>{const data=JSON.parse(event.data);if(data.id){const p=pending.get(data.id);pending.delete(data.id);data.error?p.reject(data.error):p.resolve(data.result)}else if(data.method==='Runtime.exceptionThrown')errors.push(data.params.exceptionDetails)});
function send(method, params={}){return new Promise((resolve,reject)=>{const id=++next;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}))})}
async function evaluate(expression){const result=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(result.exceptionDetails)throw Error(JSON.stringify(result.exceptionDetails));return result.result.value}
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(expression){for(let n=0;n<100;n++){if(await evaluate(expression))return;await delay(100)}throw Error('Timeout: '+expression)}
async function shot(name){const result=await send('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(output,name),Buffer.from(result.data,'base64'))}
await send('Runtime.enable'); await send('Page.enable');
await send('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false});
await send('Page.navigate',{url:url.replace(/#.*$/,'')+'#sync'});
await until("document.getElementById('updated')?.textContent.startsWith('Observed')");
await until("document.querySelectorAll('#security-table tbody tr').length===2");
assert.equal(await evaluate("document.getElementById('sync').hidden"),false);
assert.equal(await evaluate("document.querySelectorAll('#security-table button').length"),0);
assert.ok(await evaluate("document.getElementById('security-note').textContent.includes('unsigned telemetry')"));
const observed=await evaluate("({local:ops.sync.security,peers:ops.sync.peers.rows.map(p=>({peer:p.peer,security:p.security}))})");
if(expectedPolicy==='legacy'){
  assert.equal(observed.local.policy,'legacy');
  assert.equal(observed.local.verification.failed_attempts,null);
  for(const peer of observed.peers){assert.ok(['legacy','unknown'].includes(peer.security.policy));assert.equal(peer.security.verification.last_success_at,null)}
  assert.ok(await evaluate("document.getElementById('security-table').textContent.includes('legacy / not enforced')"));
}
for(const theme of ['light','dark']){
  if(await evaluate("document.body.classList.contains('dark')")!==(theme==='dark'))await evaluate("document.getElementById('theme').click()");
  assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth'),true);
  await shot('security-desktop-'+theme+'.png');
}
await evaluate("(()=>{const fixture=structuredClone(ops.sync);fixture.security.policy='required';fixture.security.verification={attempts:9,failed_attempts:2,last_success_at:'2026-10-08T12:00:00Z',last_failure_at:'2026-10-08T11:00:00Z'};fixture.peers.rows[0].security={...fixture.peers.rows[0].security,policy:'required',pairing:'approved',wizard_step:'activation',next_action:'confirm_coordinated_legacy_boundary',roundtrip:'reported verified',roundtrip_packet_uuid:'00000000-0000-4000-8000-000000000010'};fixture.peers.rows[1].security={...fixture.peers.rows[1].security,policy:'legacy',pairing:'pending',wizard_step:'pairing',next_action:'confirm_peer_fingerprint_out_of_band',roundtrip:'pending'};renderSecurity(fixture)})()");
assert.ok(await evaluate("document.getElementById('security-table').textContent.includes('approved')&&document.getElementById('security-table').textContent.includes('pending')&&document.getElementById('security-table').textContent.includes('reported verified')"));
assert.ok(await evaluate("document.getElementById('overview-security').textContent.includes('required / configured')"));
await shot('security-fixture-approved-pending.png');
await evaluate("(()=>{const fixture=structuredClone(ops.sync);fixture.peers.rows[0].security.pairing='revoked';fixture.peers.rows[1].security.pairing='unknown';renderSecurity(fixture)})()");
assert.ok(await evaluate("document.getElementById('security-table').textContent.includes('revoked')&&document.getElementById('security-table').textContent.includes('unknown')"));
await evaluate('renderSecurity(ops.sync)');
await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
for(const theme of ['light','dark']){
  if(await evaluate("document.body.classList.contains('dark')")!==(theme==='dark'))await evaluate("document.getElementById('theme').click()");
  assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth'),true);
  await shot('security-mobile-'+theme+'.png');
}
await evaluate("document.querySelector('nav [data-view=overview]').click()");
assert.ok(await evaluate("document.getElementById('overview-security').textContent.includes('Syncthing connectivity is not packet authentication')"));
assert.equal(errors.length,0,JSON.stringify(errors));
const result={verified:true,url,readOnly:true,expectedPolicy:expectedPolicy||null,observed,desktop:'1440x1100',mobile:'390x844',themes:['light','dark'],pairingFixtures:['approved','pending','revoked','unknown'],noManagementButtons:true,unknownNotZero:true,documentOverflow:false,runtimeExceptions:errors.length};
fs.writeFileSync(path.join(output,'security-qa.json'),JSON.stringify(result,null,2)+'\n');
console.log(JSON.stringify(result,null,2));ws.close();
