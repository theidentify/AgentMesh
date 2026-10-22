import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { Buffer } from 'node:buffer';
import { makeEnv,writePolicy,approved,register,start,finish,admin } from './fakes.mjs';
import { bytes,wire,sign,sha } from '../src/encoding.js';
import { duration,timestamp } from '../src/status-ui.js';
import { callTool,ulid } from '../src/gateway.js';
const {createGateway}=await import('../src/worker.js');
const rsa=await crypto.subtle.generateKey({name:'RSASSA-PKCS1-v1_5',modulusLength:2048,publicExponent:new Uint8Array([1,0,1]),hash:'SHA-256'},true,['sign','verify']);
const jwk={...await crypto.subtle.exportKey('jwk',rsa.publicKey),kid:'local',alg:'RS256',use:'sig'};
async function jwt(claims={},header={},key=rsa.privateKey){
 const enc=x=>Buffer.from(JSON.stringify(x)).toString('base64url'),now=Math.floor(Date.now()/1000);
 const data=enc({alg:'RS256',kid:'local',...header})+'.'+enc({iss:'https://team.cloudflareaccess.com',aud:['status-aud'],email:'OWNER@example.com',iat:now,nbf:now-1,exp:now+300,...claims});
 return data+'.'+Buffer.from(await crypto.subtle.sign('RSASSA-PKCS1-v1_5',key,bytes(data))).toString('base64url');
}
async function harness(){
 const h=await makeEnv();Object.assign(h.env,{STATUS_ORIGIN:'https://status.example',ACCESS_TEAM_DOMAIN:'team.cloudflareaccess.com',STATUS_ACCESS_AUD:'status-aud',STATUS_ALLOWED_EMAILS:' owner@example.com, second@example.com '});
 h.probes=0;h.jwks=0;
 h.status={device_id:h.deviceId,now_ms:Date.now(),last_control_sync_ms:null,last_drain_ms:null,last_drain_result:{stored:0,rejected:0,errors:0},outbox_cursor_ms:null};
 h.options={jwksFetcher:async url=>{assert.equal(url,'https://team.cloudflareaccess.com/cdn-cgi/access/certs');h.jwks++;return Response.json({keys:[jwk]});},fetcher:async(url,options)=>{h.probes++;assert.equal(options.headers['X-Agentmesh-App'],'0'.repeat(32));assert.deepEqual(JSON.parse(new TextDecoder().decode(options.body)),{tool:'status',arguments:{}});return Response.json(h.status);}};
 h.worker=createGateway(h.options);h.get=async(path='/status.json',token,origin=h.env.STATUS_ORIGIN,method='GET')=>h.worker.fetch(new Request(origin+path,{method,headers:token!==''?{'Cf-Access-Jwt-Assertion':token??await jwt()}: {Cookie:'CF_Authorization=ignored'}}),h.env);return h;
}
test('P6-S1 valid Access JWT renders only metadata with expected sections and headers',async()=>{
 const h=await harness(),a=await approved(h);
 await h.env.STORAGE.put('outbox/'+ulid()+'.json',bytes('{"ciphertext":"ciphertext-marker","body":"remembered-body-marker"}'));
 const get=h.env.STORAGE.get.bind(h.env.STORAGE);h.env.STORAGE.get=async key=>{assert.ok(!key.startsWith('outbox/')&&!key.startsWith('events/'));return get(key);};
 await h.env.STORAGE.put(`connectors/${a.p.app_id}/secret/test.json`,wire(await sign({format:'agentmesh-connector-secret-window-v1',app_id:a.p.app_id,not_before:Date.now()-100,not_after:Date.now()+60000,issuer_device:h.deviceId},h.device,'connector-secret-window')));
 const response=await h.get(),data=await response.json();assert.equal(response.status,200);
 for(const section of ['bridges','outbox','apps','secret_windows','gateway_errors','r2_sync'])assert.ok(Object.hasOwn(data,section));
 assert.equal(data.bridges.probe.state,'online');assert.equal(data.bridges.devices[0].device_id,h.deviceId);assert.equal(data.bridges.devices[0].live,true);
 assert.equal(data.outbox.count,1);assert.ok(data.outbox.oldest_age_ms>=0);assert.equal(data.apps[0].has_oauth_grants,true);assert.deepEqual(data.apps[0].client_names,['Claude']);assert.equal(data.secret_windows[0].app_id,a.p.app_id);
 const html=await h.get('/status');assert.equal(html.status,200);const text=await html.text();
 assertStatusHtml(html,text);
 for(const title of ['Bridges','Outbox','Apps','Pending requests','Secret windows','Gateway errors','R2 sync'])assert.ok(text.includes('<h2>'+title+'</h2>'));
 assert.doesNotMatch(text,/ciphertext-marker|remembered-body-marker/);assert.equal(h.probes,2);assert.equal(h.jwks,1);
 for(const r of [response,html]){assert.equal(r.headers.get('Cache-Control'),'no-store');assert.equal(r.headers.get('X-Frame-Options'),'DENY');assert.equal(r.headers.get('Referrer-Policy'),'no-referrer');assert.equal(r.headers.get('Access-Control-Allow-Origin'),null);assert.match(r.headers.get('Content-Security-Policy'),/default-src 'none'/);}
});
test('status routes expose only numeric Server-Timing durations',async()=>{
 const h=await harness();await approved(h);
 for(const path of ['/status','/status.json']){
  const response=await h.get(path);assert.equal(response.status,200);
  const timing=response.headers.get('Server-Timing');assert.ok(timing);
  const entries=timing.split(/,\s*/);
  assert.deepEqual(entries.map(entry=>entry.split(';')[0]),['probe','inventory','apps','errors','devices','pending','render','total']);
  for(const entry of entries)assert.match(entry,/^[a-z]+;dur=\d+(?:\.\d+)?$/);
  if(path==='/status.json')for(const name of ['pending','render'])assert.ok(entries.includes(name+';dur=0'));
 }
});
test('P6-S2 Access rejects invalid assertions uniformly before probing',async()=>{
 const h=await harness();const other=await crypto.subtle.generateKey({name:'RSASSA-PKCS1-v1_5',modulusLength:2048,publicExponent:new Uint8Array([1,0,1]),hash:'SHA-256'},true,['sign','verify']);
 const tokens=['',await jwt({}, {},other.privateKey),await jwt({aud:['wrong']}),await jwt({iss:'https://wrong.example'}),await jwt({iat:Math.floor(Date.now()/1000)-600,nbf:Math.floor(Date.now()/1000)-600,exp:Math.floor(Date.now()/1000)-120}),await jwt({exp:1}),await jwt({email:'stranger@example.com'}),await jwt({}, {kid:'unknown'}),await jwt({nbf:9999999999}),await jwt({iat:9999999999}),await jwt({exp:'9999999999'}),await jwt({}, {alg:'HS256'})];
 for(const token of tokens){const r=await h.get('/status',token);assert.equal(r.status,403);assert.equal(await r.text(),'Forbidden');assert.equal(r.headers.get('Access-Control-Allow-Origin'),null);}
 assert.equal(h.probes,0);
});
test('P6-S3 host separation covers methods, preflight and disabled feature',async()=>{
 const h=await harness();
 for(const path of ['/status','/status.json'])assert.equal((await h.get(path,await jwt(),h.env.ORIGIN)).status,404);
 for(const path of ['/mcp','/authorize','/token','/register','/admin/pending','/.well-known/oauth-authorization-server','/.well-known/oauth-protected-resource'])for(const method of ['GET','POST','OPTIONS']){const r=await h.get(path,await jwt(),h.env.STATUS_ORIGIN,method);assert.equal(r.status,404);assert.equal(r.headers.get('Access-Control-Allow-Origin'),null);}
 assert.equal((await h.get('/status',await jwt(),h.env.STATUS_ORIGIN,'POST')).status,404);
 delete h.env.STATUS_ORIGIN;assert.equal((await h.get('/status',await jwt(),'https://status.example')).status,404);
});
test('P6-S4 denied tool and HTTP errors increment bounded hourly counters',async()=>{
 const h=await harness();assert.deepEqual(await callTool(h.env,{props:{app_id:'ab'.repeat(16)},clientId:'missing'},'whoami',{}),{error:'denied'});
 await h.worker.fetch(new Request(h.env.ORIGIN+'/mcp'),h.env);
 const data=await (await h.get()).json();assert.equal(data.gateway_errors.denied,1);assert.equal(data.gateway_errors.invalid_token,1);
 for(const [key,row]of h.env.OAUTH_KV.data)if(key.startsWith('status:err:')){assert.match(key,/status:err:\d{10}$/);assert.ok(row.expires<=Date.now()+172800000);}
});
test('P6-S5 probe failures expose only fixed reasons; tool calls write no bridge record',async()=>{
 const h=await harness(),{p}=await writePolicy(h,'client');
 await callTool(h.env,{props:{app_id:p.app_id},clientId:'client'},'recall',{query:'private'},async()=>Response.json({device_id:'ff'.repeat(16),data:{body:'remembered-body-marker'}}));
 assert.equal(await h.env.OAUTH_KV.get('status:bridge:unattributed'),null);
 for(const [status,reason]of [[502,'offline'],[403,'denied'],[500,'unavailable']]){h.worker=createGateway({...h.options,fetcher:async()=>new Response(null,{status})});const data=await (await h.get()).json();assert.equal(data.bridges.probe.state,'offline');assert.equal(data.bridges.probe.reason,reason);assert.ok(!JSON.stringify(data).includes('remembered-body-marker'));}
});
test('P6-S9 JWKS rotation, cache expiry and fetch failure fail closed',async()=>{
 const h=await harness();assert.equal((await h.get()).status,200);assert.equal(h.jwks,1);
 assert.equal((await h.get('/status',await jwt({}, {kid:'unknown'}))).status,403);assert.equal(h.jwks,2);
 const rotated={...jwk,kid:'rotated'};let calls=0;
 h.worker=createGateway({...h.options,jwksFetcher:async()=>{calls++;return Response.json({keys:[calls===1?jwk:rotated]});}});
 assert.equal((await h.get()).status,200);assert.equal((await h.get('/status',await jwt({}, {kid:'rotated'}))).status,200);assert.equal(calls,2);
 const realNow=Date.now;try{Date.now=()=>realNow()+301000;assert.equal((await h.get('/status',await jwt({}, {kid:'rotated'}))).status,200);assert.equal(calls,3);}finally{Date.now=realNow;}
 h.worker=createGateway({...h.options,jwksFetcher:async()=>{throw Error('private failure');}});const r=await h.get();assert.equal(r.status,403);assert.equal(await r.text(),'Forbidden');
});
test('P6-S10 R2 pagination, metadata dates and 10k cap do not read encrypted objects',async()=>{
 const h=await harness(),at=Date.now();let calls=0;
 h.env.STORAGE.list=async({prefix,cursor,limit})=>{calls++;if(prefix!=='events/')return {objects:[],truncated:false};const start=Number(cursor??0),end=Math.min(start+limit,10001);return {objects:Array.from({length:end-start},(_,i)=>({key:`events/${start+i}`,uploaded:new Date(at-(10001-start-i)*1000)})),truncated:end<10001,cursor:String(end)};};
 h.env.STORAGE.get=async()=>{assert.fail('inventory must not read object contents');};
 const data=await (await h.get()).json();assert.equal(data.r2_sync.counts['events/'],10000);assert.equal(data.r2_sync.capped,true);assert.equal(data.apps_capped,true);assert.equal(data.r2_sync.latest_event_uploaded_ms,at-2000);assert.ok(calls<20);
});
test('P6-S11 HTML escapes client names and status projection discards extra fields',async()=>{
 const h=await harness(),{p}=await writePolicy(h,'client');h.status.body='remembered-body-marker';h.status.ciphertext='ciphertext-marker';
 h.worker=createGateway({...h.options,authorizationServer:()=>({getOAuthApi:()=>({listUserGrants:async()=>({items:[{clientId:'client'}]}),lookupClient:async()=>({clientName:'<img src=x onerror="attack">'})})})});
 const html=await (await h.get('/status')).text();assert.ok(html.includes('&lt;img'));assert.doesNotMatch(html,/<img|remembered-body-marker|ciphertext-marker/);
 const data=await (await h.get()).json();assert.equal(data.apps[0].app_id,p.app_id);assert.equal(data.bridges.probe.body,undefined);
});
test('P6-S12 failed or stalled telemetry never blocks tool responses',async()=>{
 const h=await harness();h.env.OAUTH_KV.get=()=>new Promise(()=>{});h.env.STATUS_WAIT_UNTIL=()=>{};
 const result=await Promise.race([callTool(h.env,{props:{app_id:'ab'.repeat(16)},clientId:'client'},'whoami',{}),new Promise(resolve=>setTimeout(()=>resolve('blocked'),100))]);assert.deepEqual(result,{error:'denied'});
 h.env.OAUTH_KV.get=async()=>{throw Error('private failure');};assert.deepEqual(await callTool(h.env,{props:{app_id:'ab'.repeat(16)},clientId:'client'},'whoami',{}),{error:'denied'});
});
test('P6-S13 control reads are bounded and consumed streams can be reused',async()=>{
 const h=await harness();await writePolicy(h,'first');await writePolicy(h,'second');
 const get=h.env.STORAGE.get.bind(h.env.STORAGE),seen=new Set();
 h.env.STORAGE.get=async key=>{assert.ok(!seen.has(key));seen.add(key);const object=await get(key);if(!object)return null;const response=new Response(await object.arrayBuffer());return {arrayBuffer:()=>response.arrayBuffer()};};
 h.worker=createGateway({...h.options,authorizationServer:()=>({getOAuthApi:()=>({listUserGrants:async()=>({items:[{clientId:'expired',expiresAt:1}]}),lookupClient:async()=>assert.fail('expired grants must not resolve clients')})})});
 const data=await (await h.get()).json();assert.equal(data.apps.length,2);assert.ok(data.apps.every(a=>!a.has_oauth_grants));
});

async function actionPost(h,fields,headers={},origin=h.env.STATUS_ORIGIN){
 return h.worker.fetch(new Request(origin+'/status/action',{method:'POST',headers:{'Cf-Access-Jwt-Assertion':await jwt({sub:'owner'}),Origin:h.env.STATUS_ORIGIN,'Sec-Fetch-Site':'same-origin',...headers},body:new URLSearchParams(fields)}),h.env);
}
async function actionToken(h){
 const html=await (await h.get('/status',await jwt({sub:'owner'}))).text();
 const token=/name="csrf" value="([0-9a-f]{64})"/.exec(html)?.[1];assert.ok(token);return token;
}
async function actionHarness(){
 const h=await harness();await h.env.OAUTH_KV.put('pending:'+'cd'.repeat(32),JSON.stringify({client_name:'<request>',created:Date.now(),expires:Date.now()+600000,browser_hash:'ef'.repeat(32),auth:{body:'private-marker'}}));
 h.actions=[];h.worker=createGateway({...h.options,fetcher:async(url,options)=>{
  const body=JSON.parse(new TextDecoder().decode(options.body));if(body.tool==='status')return h.options.fetcher(url,options);
  assert.equal(options.headers['X-Agentmesh-App'],'0'.repeat(32));h.actions.push(body);
  return Response.json(await sign({format:'agentmesh-connector-admin-v1',device_id:h.deviceId,ts:Date.now(),nonce:crypto.randomUUID().replaceAll('-',''),action:body.arguments.action,payload:{code_hash:body.arguments.code_hash}},h.device,'connector-admin'));
 }});return h;
}
test('P6-S10 actions bind CSRF to JWT identity and reject bad requests before bridge',async()=>{
 const h=await actionHarness(),csrf=await actionToken(h),fields={csrf,action:'reject',code_hash:'cd'.repeat(32)};
 for(const [values,headers]of [[{...fields,csrf:''},{}],[{...fields,csrf:'00'.repeat(32)},{}],[fields,{Origin:'https://evil.example'}],[fields,{'Sec-Fetch-Site':'cross-site'}],[fields,{'Cf-Access-Jwt-Assertion':''}],[fields,{'Cf-Access-Jwt-Assertion':await jwt({sub:'another'})}],[fields,{'Cf-Access-Jwt-Assertion':await jwt({sub:'owner',email:'second@example.com'})}]]){
  assert.equal((await actionPost(h,values,headers)).status,403);assert.equal(h.actions.length,0);
 }
 assert.equal((await actionPost(h,fields,{},h.env.ORIGIN)).status,404);
 const completed=await actionPost(h,fields);assert.equal(completed.status,200);assertStatusHtml(completed,await completed.text());assert.equal(h.actions.length,1);assert.equal(await h.env.OAUTH_KV.get('status:inventory'),null);
 assert.deepEqual(h.actions[0],{tool:'admin',arguments:{action:'reject',code_hash:'cd'.repeat(32)}});
 assert.equal(await h.env.OAUTH_KV.get('pending:'+'cd'.repeat(32)),null);
 assert.equal((await actionPost(h,fields)).status,403);assert.equal(h.actions.length,1);
});
test('P6-S14 reject admin requires pinned device signature and refuses replay',async()=>{
 const h=await actionHarness(),payload={code_hash:'cd'.repeat(32)};
 const envelope=await sign({format:'agentmesh-connector-admin-v1',device_id:h.deviceId,ts:Date.now(),nonce:'aa'.repeat(16),action:'reject',payload},h.device,'connector-admin');
 const send=r=>h.worker.fetch(new Request(h.env.ORIGIN+'/admin/reject',{method:'POST',body:wire(r)}),h.env);
 assert.equal((await send({...envelope,signature:'00'.repeat(64)})).status,403);
 assert.equal((await send({...envelope,device_id:'ff'.repeat(16)})).status,403);
 assert.equal((await send(envelope)).status,200);
 assert.equal((await send(envelope)).status,403);
});

test('P6-S15 CSRF expiration, duplicates, strict arguments and concurrent single use',async()=>{
 const h=await actionHarness();let csrf=await actionToken(h),fields={csrf,action:'reject',code_hash:'cd'.repeat(32)};
 for(const [body,headers]of [[fields,{Origin:''}],[{...fields,action:'approve'},{}],[{...fields,app_id:'ab'.repeat(16)},{}],[{...fields,code_hash:'no'},{}]]){
  assert.equal((await actionPost(h,body,headers)).status,403);assert.equal(h.actions.length,0);
 }
 const key=[...h.env.OAUTH_KV.data.keys()].find(k=>k.startsWith('status:csrf:'));
 const row=await h.env.OAUTH_KV.get(key,'json');await h.env.OAUTH_KV.put(key,JSON.stringify({...row,exp:Date.now()-1}));
 assert.equal((await actionPost(h,fields)).status,403);assert.equal(h.actions.length,0);
 csrf=await actionToken(h);fields={...fields,csrf};
 assert.equal((await actionPost(h,[...Object.entries(fields),['action','reject']])).status,403);
 const results=await Promise.all([actionPost(h,fields),actionPost(h,fields)]);
 assert.deepEqual(results.map(r=>r.status).sort(),[200,403]);assert.equal(h.actions.length,1);
});
async function revokeDevice(h){
 const other=await crypto.subtle.generateKey('Ed25519',true,['sign','verify']),id='09'.repeat(16),issued_at=Date.now()-5000;
 const certificate=await sign({format:'agentmesh-device-cert-v1',account_id:JSON.parse(h.env.ACCOUNT_PUBLIC_RECORD).account_id,device_id:id,device_public_key:Buffer.from(await crypto.subtle.exportKey('raw',other.publicKey)).toString('hex'),issuer:'account',issued_at},h.root.privateKey,'device-cert');
 await h.env.STORAGE.put(`registry/devices/${id}.json`,wire(await sign({format:'agentmesh-device-record-v1',certificate,age_recipient:'age1synthetic',issued_at},other.privateKey,'device-record')));
 await h.env.STORAGE.put(`registry/revocations/${h.deviceId}.json`,wire(await sign({format:'agentmesh-device-revocation-v1',device_id:h.deviceId,revoked_by:id,revoked_at:Date.now()-1,reason_code:'removed'},other.privateKey,'device-revocation')));
}
for(const action of ['reject','revoke'])test('P6-S16 '+action+' envelopes reject forgery, unpinned/revoked signers and replay',async()=>{
 const h=await actionHarness();let payload={code_hash:'cd'.repeat(32)},drops=0;
 if(action==='revoke'){
  const {p,ref}=await writePolicy(h,'client',{state:'revoked'});payload={app_id:p.app_id,policy_ref:ref};
  h.worker=createGateway({...h.options,authorizationServer:()=>({getOAuthApi:()=>({listUserGrants:async()=>({items:[{id:'grant'}]}),revokeGrant:async()=>{drops++;}})})});
 }
 const make=extra=>sign({format:'agentmesh-connector-admin-v1',device_id:h.deviceId,ts:Date.now(),nonce:crypto.randomUUID().replaceAll('-',''),action,payload,...extra},h.device,'connector-admin');
 const send=r=>h.worker.fetch(new Request(h.env.ORIGIN+'/admin/'+action,{method:'POST',body:wire(r)}),h.env);
 const r=await make();
 for(const bad of [{...r,signature:'00'.repeat(64)},await make({device_id:'ff'.repeat(16)}),await make({payload:{...payload,approve:true}})])assert.equal((await send(bad)).status,403);
 assert.equal(drops,0);assert.equal((await send(r)).status,200);assert.equal((await send(r)).status,403);
 if(action==='revoke')assert.equal(drops,1);
 await revokeDevice(h);assert.equal((await send(await make())).status,403);
});
test('P6-S17 action forms contain only allowed projections and relay failure is fixed',async()=>{
 const h=await actionHarness(),{p}=await writePolicy(h,'client');
 await h.env.STORAGE.put(`connectors/${p.app_id}/secret/test.json`,wire(await sign({format:'agentmesh-connector-secret-window-v1',app_id:p.app_id,not_before:Date.now()-100,not_after:Date.now()+60000,issuer_device:h.deviceId},h.device,'connector-secret-window')));
 const html=await (await h.get('/status',await jwt({sub:'owner'}))).text();
 for(const action of ['reject','revoke','close_secret'])assert.ok(html.includes(`name="action" value="${action}"`));
 assert.doesNotMatch(html,/private-marker|<request>/);assert.match(html,/&lt;request&gt;/);
 const csrf=await actionToken(h);h.worker=createGateway({...h.options,fetcher:async()=>Response.json({error:'private-marker'})});
 const result=await actionPost(h,{csrf,action:'close_secret',app_id:p.app_id});assert.equal(result.status,403);assert.equal(await result.text(),'Forbidden');
});

test('P6-S21 revoke and close relay only exact reductions; mismatched envelope is refused',async()=>{
 const h=await actionHarness(),{p}=await writePolicy(h,'client');let seen=[];
 h.worker=createGateway({...h.options,fetcher:async(url,options)=>{
  const body=JSON.parse(new TextDecoder().decode(options.body));if(body.tool==='status')return h.options.fetcher(url,options);seen.push(body);
  if(body.arguments.action==='close_secret')return Response.json({status:'ok'});
  const {ref}=await writePolicy(h,'client',{...p,state:'revoked',issued_at:Date.now()});
  return Response.json(await sign({format:'agentmesh-connector-admin-v1',device_id:h.deviceId,ts:Date.now(),nonce:crypto.randomUUID().replaceAll('-',''),action:'revoke',payload:{app_id:p.app_id,policy_ref:ref}},h.device,'connector-admin'));
 }});
 for(const action of ['close_secret','revoke']){const csrf=await actionToken(h);assert.ok(await h.env.OAUTH_KV.get('status:inventory'));assert.equal((await actionPost(h,{csrf,action,app_id:p.app_id})).status,200);assert.equal(await h.env.OAUTH_KV.get('status:inventory'),null);}
 assert.deepEqual(seen.map(r=>r.arguments),[{action:'close_secret',app_id:p.app_id},{action:'revoke',app_id:p.app_id}]);
 const csrf=await actionToken(h);
 assert.equal((await actionPost(h,{csrf,action:'reject',code_hash:'cd'.repeat(32)})).status,403);
 assert.ok(await h.env.OAUTH_KV.get('pending:'+'cd'.repeat(32)));
});
test('P6-S22 reject prevents completion even if a stale approval resurrects KV',async()=>{
 const h=await harness(),client=await register(h),pending=await start(h,client.client_id),code_hash=await sha(bytes(pending.code));
 const {p,ref}=await writePolicy(h,client.client_id);
 assert.equal((await admin(h,'approve',{code_hash,app_id:p.app_id,policy_ref:ref})).status,200);
 const stale=await h.env.OAUTH_KV.get('pending:'+code_hash);
 assert.equal((await admin(h,'reject',{code_hash})).status,200);
 await h.env.OAUTH_KV.put('pending:'+code_hash,stale);
 assert.equal((await finish(h,pending)).status,403);
});

test('P6-P6 passkey pages and script are status-only and require JWT',async()=>{
 const h=await harness();
 for(const path of ['/status/passkeys','/status/approve','/status/webauthn.js']){
  assert.equal((await h.get(path,'')).status,403);
  assert.equal((await h.get(path,await jwt(),h.env.ORIGIN)).status,404);
  const r=await h.get(path,await jwt({sub:'owner'}));assert.equal(r.status,200);
  assert.equal(r.headers.get('Cache-Control'),'no-store');assert.equal(r.headers.get('Access-Control-Allow-Origin'),null);
  if(!path.endsWith('.js')){const html=await r.text();assertStatusHtml(r,html,true);assert.match(html,/name="csrf"/);assert.match(html,/<noscript>/);assert.match(html,/id="readiness"/);assert.match(html,/data-origin="https:/);assert.match(html,/\/status\/webauthn.js/);}
 }
 assert.equal(h.probes,0);
});

test('P6-P6 passkey relay needs single-use CSRF and cannot grant forged approval',async()=>{
 const h=await harness(),client=await register(h),pending=await start(h,client.client_id),token=await jwt({sub:'owner'});let calls=0;
 h.worker=createGateway({...h.options,fetcher:async()=>{calls++;return Response.json({action:'approve',payload:{code_hash:await sha(bytes(pending.code))}});}});
 async function csrf(){const r=await h.get('/status/approve',token);return /name="csrf" value="([0-9a-f]+)"/.exec(await r.text())?.[1];}
 const post=(body,origin=h.env.STATUS_ORIGIN)=>h.worker.fetch(new Request(h.env.STATUS_ORIGIN+'/status/approve',{method:'POST',headers:{'Cf-Access-Jwt-Assertion':token,'Origin':origin,'Content-Type':'application/json'},body:JSON.stringify(body)}),h.env);
 const args={step:'approve_finish',code_hash:await sha(bytes(pending.code)),device_id:h.deviceId,challenge_id:'ab'.repeat(32),assertion:{id:'assertion-marker'}};
 assert.equal((await post(args)).status,403);assert.equal(calls,0);
 const value=await csrf();assert.ok(value);
 assert.equal((await post({...args,csrf:value},'https://evil.example')).status,403);assert.equal(calls,0);
 assert.equal((await post({...args,csrf:value})).status,403);assert.equal(calls,1);
 assert.equal((await post({...args,csrf:value})).status,403);assert.equal(calls,1);
 const stored=await h.env.OAUTH_KV.get('pending:'+args.code_hash,'json');assert.ok(!stored.approval);
 for(const row of h.env.OAUTH_KV.data.values())assert.ok(!JSON.stringify(row).includes('assertion-marker'));
});

test('P6-P6 approval begin relays hashes and returns transient names; finish applies signed envelope',async()=>{
 const h=await harness(),client=await register(h),flow=await start(h,client.client_id),token=await jwt({sub:'owner'}),codeHash=await sha(bytes(flow.code));
 const {p,ref}=await writePolicy(h,client.client_id);let seen;
 const policy=Object.fromEntries(['read','remember','max_label','allow_cross_profile','limits'].map(k=>[k,p[k]]));
 const envelope=await sign({format:'agentmesh-connector-admin-v1',device_id:h.deviceId,ts:Date.now(),nonce:'ab'.repeat(16),action:'approve',payload:{code_hash:codeHash,app_id:p.app_id,policy_ref:ref}},h.device,'connector-admin');
 h.worker=createGateway({...h.options,fetcher:async(url,options)=>{
  seen=JSON.parse(new TextDecoder().decode(options.body));assert.equal(seen.tool,'passkey');
  if(seen.arguments.step==='approve_finish')return Response.json(envelope);
  return Response.json({options:{rpId:'status.example',challenge:'YQ',userVerification:'required'},device_id:h.deviceId,challenge_id:'ab'.repeat(32),policy,profile_names:['private-profile-marker'],remember_name:'private-profile-marker/inbox'});
 }});
 const html=await (await h.get('/status/approve',token)).text();const csrf=/name="csrf" value="([0-9a-f]+)"/.exec(html)[1];
 const post=body=>h.worker.fetch(new Request(h.env.STATUS_ORIGIN+'/status/approve',{method:'POST',headers:{'Cf-Access-Jwt-Assertion':token,Origin:h.env.STATUS_ORIGIN,'Content-Type':'application/json'},body:JSON.stringify(body)}),h.env);
 const result=await (await post({step:'approve_begin',code:flow.code,csrf})).json();
 assert.deepEqual(seen.arguments,{step:'approve_begin',code_hash:codeHash,client_id_hash:await sha(bytes(client.client_id)),client_name:'Claude'});
 assert.deepEqual(result.profile_names,['private-profile-marker']);assert.equal(result.client_name,'Claude');
 const finishArgs={step:'approve_finish',code_hash:codeHash,device_id:h.deviceId,challenge_id:result.challenge_id,assertion:{id:'assertion-marker'},csrf:result.csrf};
 await h.env.OAUTH_KV.put('status:inventory','cached');
 assert.equal((await post(finishArgs)).status,200);assert.equal(await h.env.OAUTH_KV.get('status:inventory'),null);
 assert.ok((await h.env.OAUTH_KV.get('pending:'+codeHash,'json')).approval);
 assert.equal((await finish(h,flow)).status,302);
 for(const row of h.env.OAUTH_KV.data.values())assert.doesNotMatch(JSON.stringify(row),/assertion-marker|private-profile-marker/);
});

test('P6-P6 offline and wrong-device outcomes are fixed and never grant',async()=>{
 for(const [status,body,expected]of [[502,null,'agentmesh_offline'],[403,{error:'wrong_device'},'wrong_device'],[200,{error:'private-error-marker'},'denied']]){
  const h=await harness(),token=await jwt({sub:'owner'});
  h.worker=createGateway({...h.options,fetcher:async()=>body?Response.json(body,{status}):new Response(null,{status})});
  const html=await (await h.get('/status/passkeys',token)).text(),csrf=/name="csrf" value="([0-9a-f]+)"/.exec(html)[1];
  const r=await h.worker.fetch(new Request(h.env.STATUS_ORIGIN+'/status/passkeys',{method:'POST',headers:{'Cf-Access-Jwt-Assertion':token,Origin:h.env.STATUS_ORIGIN,'Content-Type':'application/json'},body:JSON.stringify({csrf,step:'register_begin',code:'ABCDEFGHJK'})}),h.env);
  assert.deepEqual(await r.json(),{error:expected});
  for(const row of h.env.OAUTH_KV.data.values())assert.doesNotMatch(JSON.stringify(row),/ABCDEFGHJK|private-error-marker/);
 }
});

const htmlCsp="default-src 'none'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'";
function assertStatusHtml(response,html,passkey=false){
 assert.equal(response.headers.get('Content-Security-Policy'),passkey?htmlCsp.replace("style-src 'self';", "style-src 'self'; script-src 'self'; connect-src 'self';"):htmlCsp);
 assert.match(html,/<link rel="stylesheet" href="\/status\/style.css">/);
 assert.match(html,/<small>STATUS<\/small>/);assert.match(html,/name="viewport"/);
 assert.doesNotMatch(html,/<pre|<style| style=| on[a-z]+=|data:|<script(?! src="\/status\/webauthn.js" defer><\/script>)/);
 if(!passkey)assert.doesNotMatch(html,/<script/);
}
test('Insight stylesheet and root redirect require Access and remain status-host only',async()=>{
 const h=await harness(),token=await jwt({sub:'owner'});
 for(const path of ['/','/status/style.css']){
  for(const bad of ['',await jwt({aud:['wrong']})])assert.equal((await h.get(path,bad)).status,403);
  assert.equal((await h.get(path,token,h.env.ORIGIN)).status,404);
  for(const method of ['POST','OPTIONS','HEAD'])assert.equal((await h.get(path,token,h.env.STATUS_ORIGIN,method)).status,404);
 }
 const redirect=await h.get('/',token);assert.equal(redirect.status,302);assert.equal(redirect.headers.get('Location'),'/status');
 const css=await h.get('/status/style.css',token);assert.equal(css.status,200);assert.equal(css.headers.get('Content-Type'),'text/css; charset=utf-8');assert.equal(css.headers.get('Cache-Control'),'no-store');assert.equal(css.headers.get('Access-Control-Allow-Origin'),null);
 const style=await css.text();assert.match(style,/@media\(prefers-color-scheme:dark\)/);assert.match(style,/@media\(max-width:650px\)/);assert.match(style,/overflow-x:auto/);assert.doesNotMatch(style,/@import|url\(/);
 for(const path of ['/status/unknown','/status/style.css/','/status/action'])assert.equal((await h.get(path,token)).status,404);
 assert.equal(h.probes,0);
});
test('Insight empty states and native revoke confirmation preserve action fields',async()=>{
 const h=await harness();let html=await (await h.get('/status',await jwt({sub:'owner'}))).text();
 for(const text of ['No apps','No pending requests','No active secret windows'])assert.ok(html.includes(text));
 await writePolicy(h,'client');await h.env.OAUTH_KV.delete('status:inventory');html=await (await h.get('/status',await jwt({sub:'owner'}))).text();
 assert.match(html,/<details><summary class="danger">Revoke<\/summary>/);assert.match(html,/>Confirm revoke<\/button>/);assert.match(html,/method="post" action="\/status\/action"/);
});
// Fixture captured with statusData from 902bab2 (the passkey parent), with a fixed clock.
test('status.json remains deep-equal and byte-equal to the pre-Insight metadata fixture',async()=>{
 const realNow=Date.now;Date.now=()=>1792627200000;
 try{
  const h=await harness();await writePolicy(h,'client',{app_id:'ab'.repeat(16)});
  await h.env.STORAGE.put('outbox/01K7ZHHH0000000000000000000.json',bytes('not read'));
  await h.env.STORAGE.put('events/test',bytes('not read'));
  await h.env.STORAGE.put('connectors/'+'ab'.repeat(16)+'/secret/test.json',wire(await sign({format:'agentmesh-connector-secret-window-v1',app_id:'ab'.repeat(16),not_before:Date.now()-100,not_after:Date.now()+60000,issuer_device:h.deviceId},h.device,'connector-secret-window')));
  h.worker=createGateway({...h.options,authorizationServer:()=>({getOAuthApi:()=>({listUserGrants:async()=>({items:[{clientId:'client'}]}),lookupClient:async()=>({clientName:'Claude'})})})});
  const {readFile}=await import('node:fs/promises'),previous=await readFile(new URL('./fixtures/status-before-insight.json',import.meta.url),'utf8');
  for(let i=0;i<2;i++){const response=await h.get(),text=await response.text();assert.equal(response.status,200);assert.deepEqual(JSON.parse(text),JSON.parse(previous));assert.equal(text,previous.trim());}
 }finally{Date.now=realNow;}
});

test('duration and timestamp use singular units for one and plural units otherwise',()=>{
 const now=1792627200000;
 for(const [ms,unit]of [[1000,'sec'],[60000,'min'],[3600000,'hr'],[86400000,'day']]){
  assert.equal(duration(ms),'1 '+unit);assert.equal(duration(ms*1.5),'1 '+unit);assert.equal(duration(ms*2),'2 '+unit+'s');
  assert.ok(timestamp(now-ms,now).includes('>1 '+unit+' ago</time>'));
  assert.ok(timestamp(now+ms,now).includes('>in 1 '+unit+'</time>'));
  assert.ok(timestamp(now-ms,now,true).includes('title="'+new Date(now-ms).toISOString()+'"'));
 }
 assert.equal(duration(0),'0 ms');assert.equal(duration(null),'Unknown');assert.equal(timestamp(null,now),'Unknown');
 assert.ok(timestamp(now,now).includes('>just now</time>'));
});

 test('merged hourly counters preserve fixed codes and legacy history',async()=>{
 const {countError,hour}=await import('../src/status-metrics.js'),h=await harness(),tasks=[];
 h.env.STATUS_WAIT_UNTIL=p=>tasks.push(p);
 const key='status:err:'+hour(Date.now());
 await h.env.OAUTH_KV.put(key,JSON.stringify({denied:2,unknown:99}));
 countError(h.env,'denied');await Promise.all(tasks);
 assert.deepEqual(await h.env.OAUTH_KV.get(key,'json'),{denied:3});
 await h.env.OAUTH_KV.put('status:err:denied:'+hour(Date.now()-3600000),'4');
 await h.env.OAUTH_KV.put('status:err:'+hour(Date.now()-23*3600000),JSON.stringify({denied:5,invalid_token:2}));
 await h.env.OAUTH_KV.put('status:err:'+hour(Date.now()-24*3600000),JSON.stringify({denied:100}));
 const data=await (await h.get()).json();assert.equal(data.gateway_errors.denied,12);assert.equal(data.gateway_errors.invalid_token,2);assert.equal(data.gateway_errors.unknown,undefined);
 });
 test('inventory cache avoids lists for 45 seconds while live observations and errors stay fresh',async()=>{
 const h=await harness(),list=h.env.STORAGE.list.bind(h.env.STORAGE);let calls=0;
 h.env.STORAGE.list=async args=>{calls++;return list(args);};
 await h.get();const initial=calls;assert.equal(initial,7);
 await h.env.STORAGE.put('events/new',bytes('not read'));
 const {hour}=await import('../src/status-metrics.js');await h.env.OAUTH_KV.put('status:err:'+hour(Date.now()),JSON.stringify({denied:7}));
 await h.env.OAUTH_KV.put('status:bridge:'+'ab'.repeat(16),JSON.stringify({device_id:'ab'.repeat(16),last_seen_ms:1,latency_ms:2}));
 const data=await (await h.get()).json();assert.equal(calls,initial);assert.equal(h.probes,2);assert.equal(data.gateway_errors.denied,7);assert.ok(data.bridges.devices.some(d=>d.device_id==='ab'.repeat(16)));assert.equal(data.r2_sync.counts['events/'],0);
 const realNow=Date.now;try{Date.now=()=>realNow()+45000;const fresh=await (await h.get()).json();assert.equal(fresh.r2_sync.counts['events/'],1);assert.equal(calls,initial*2);}finally{Date.now=realNow;}
 });
 test('parallel inventory retains fixed prefix order, exact cap and cached partial authority',{timeout:2000},async()=>{
 const h=await harness();let starts=[],release;const barrier=new Promise(r=>release=r);
 const prefixes=['registry/','keyring/','keys/','events/','connectors/','outbox/'];
 h.env.STORAGE.list=async({prefix,cursor,limit})=>{
 if(!cursor){starts.push(prefix);if(starts.length===6)release();await barrier;}
 const start=Number(cursor??0),end=Math.min(start+limit,2500);
 return {objects:Array.from({length:end-start},(_,i)=>({key:prefix+String(start+i).padStart(5,'0'),uploaded:new Date(0)})),truncated:end<2500,cursor:String(end)};
 };
 const response=await h.get();assert.equal(response.status,200);const data=await response.json();
 assert.deepEqual(starts,prefixes);assert.deepEqual(Object.values(data.r2_sync.counts),[2500,2500,2500,2500,0,0]);assert.equal(data.apps_capped,true);
 const cached=await h.env.OAUTH_KV.get('status:inventory','json');assert.equal(cached.objects.length,10000);assert.equal(cached.objects[9999].key,'events/02499');
 h.env.STORAGE.list=async()=>assert.fail('cached');h.env.STORAGE.get=async()=>assert.fail('partial controls must never resolve');
 const again=await (await h.get()).json();assert.equal(again.r2_sync.capped,true);assert.equal(again.apps_capped,true);assert.deepEqual(again.apps,[]);
 });

test('HTML starts probe, inventory, error, device and pending work concurrently', {timeout:2000},async()=>{
 const h=await harness(),started=new Set();let release;const barrier=new Promise(r=>release=r);
 const arrive=async name=>{started.add(name);if(started.size===5)release();await barrier;};
 const list=h.env.STORAGE.list.bind(h.env.STORAGE),get=h.env.OAUTH_KV.get.bind(h.env.OAUTH_KV),kvlist=h.env.OAUTH_KV.list.bind(h.env.OAUTH_KV);
 h.env.STORAGE.list=async args=>{await arrive('inventory');return list(args);};
 h.env.OAUTH_KV.get=async(key,type)=>{if(/^status:err:\d/.test(key))await arrive('errors');return get(key,type);};
 h.env.OAUTH_KV.list=async args=>{await arrive(args.prefix==='pending:'?'pending':'devices');return kvlist(args);};
 h.worker=createGateway({...h.options,fetcher:async(...args)=>{await arrive('probe');return h.options.fetcher(...args);}});
 assert.equal((await h.get('/status')).status,200);assert.equal(started.size,5);
});
test('apps resolve grants concurrently and retain inventory order', {timeout:2000},async()=>{
 const h=await harness();await writePolicy(h,'one',{app_id:'aa'.repeat(16)});await writePolicy(h,'two',{app_id:'bb'.repeat(16)});
 let calls=0,release;const barrier=new Promise(r=>release=r);
 h.worker=createGateway({...h.options,authorizationServer:()=>({getOAuthApi:()=>({listUserGrants:async()=>{if(++calls===2)release();await barrier;return {items:[]};}})})});
 const data=await (await h.get()).json();assert.deepEqual(data.apps.map(a=>a.app_id),['aa'.repeat(16),'bb'.repeat(16)]);
});
test('all 24 merged hour reads start together and invalid counts are ignored', {timeout:2000},async()=>{
 const {errorCounts}=await import('../src/status-metrics.js');let release;const barrier=new Promise(r=>release=r),merged=[],legacy=[];
 const env={OAUTH_KV:{get:async(key,type)=>{
  if(type==='json'){merged.push(key);if(merged.length===24)release();await barrier;return {denied:1,unavailable:-1,invalid_token:'2',unknown:99};}
  legacy.push(key);return '0';
 }}};
 const result=await errorCounts(env,1792627200000);assert.equal(new Set(merged).size,24);assert.equal(legacy.length,144);assert.equal(result.denied,24);assert.equal(result.unavailable,0);assert.equal(result.invalid_token,0);assert.equal(result.unknown,undefined);
});
test('app cap stays at 100 and registry verification is shared within the request',async()=>{
 const h=await harness();
 for(let i=1;i<=101;i++)await writePolicy(h,'client',{app_id:i.toString(16).padStart(32,'0')});
 const verify=crypto.subtle.verify;let certificates=0;
 crypto.subtle.verify=async function(...args){if(new TextDecoder().decode(args[3]).startsWith('AgentMesh/device-cert/'))certificates++;return verify.apply(this,args);};
 try{const data=await (await h.get()).json();assert.equal(data.apps.length,100);assert.equal(data.apps_capped,true);assert.equal(data.apps[99].app_id,(100).toString(16).padStart(32,'0'));assert.equal(certificates,2);}finally{crypto.subtle.verify=verify;}
});
test('truncated inventory without a cursor stays partial on cache hits',async()=>{
 const h=await harness();await writePolicy(h,'client');const list=h.env.STORAGE.list.bind(h.env.STORAGE);
 h.env.STORAGE.list=async args=>args.prefix==='registry/'?{objects:[],truncated:true}:list(args);
 for(let i=0;i<2;i++){const data=await (await h.get()).json();assert.equal(data.r2_sync.capped,true);assert.equal(data.apps_capped,true);assert.deepEqual(data.apps,[]);h.env.STORAGE.list=async()=>assert.fail('cache hit must retain partial flag');}
});

test('app snapshot skips policy, window and OAuth reads on hits and filters expired windows',async()=>{
 const realNow=Date.now;let now=realNow();Date.now=()=>now;
 try{
  const h=await harness(),{p}=await writePolicy(h,'client');
  const window={app_id:p.app_id,not_before:now-100,not_after:now+1000};
  await h.env.STORAGE.put(`connectors/${p.app_id}/secret/test.json`,wire(await sign({format:'agentmesh-connector-secret-window-v1',...window,issuer_device:h.deviceId},h.device,'connector-secret-window')));
  // Count the policy/window resolvers' backing reads through FakeR2 and OAuth calls through fakes.
  const calls={policy:0,secretWindow:0,listUserGrants:0,lookupClient:0},get=h.env.STORAGE.get.bind(h.env.STORAGE);
  h.env.STORAGE.get=async key=>{if(key.includes('/policy/'))calls.policy++;if(key.includes('/secret/'))calls.secretWindow++;return get(key);};
  h.worker=createGateway({...h.options,authorizationServer:()=>({getOAuthApi:()=>({listUserGrants:async()=>{calls.listUserGrants++;return {items:[{clientId:'client'}]};},lookupClient:async()=>{calls.lookupClient++;return {clientName:'Claude'};}})})});
  const first=await (await h.get()).json();assert.equal(first.secret_windows.length,1);
  assert.deepEqual(calls,{policy:1,secretWindow:1,listUserGrants:1,lookupClient:1});
  now+=1000;
  const hit=await (await h.get()).json();assert.deepEqual(hit.apps,first.apps);assert.deepEqual(hit.secret_windows,[]);
  assert.deepEqual(calls,{policy:1,secretWindow:1,listUserGrants:1,lookupClient:1});
  const cached=await h.env.OAUTH_KV.get('status:inventory','json');assert.deepEqual(cached.secret_windows,[window]);assert.deepEqual(cached.apps,first.apps);
  now+=44000;await h.get();assert.deepEqual(calls,{policy:2,secretWindow:2,listUserGrants:2,lookupClient:2});
 }finally{Date.now=realNow;}
});
test('grant pagination partial flag survives an app snapshot hit',async()=>{
 const h=await harness();await writePolicy(h,'client');let calls=0;
 h.worker=createGateway({...h.options,authorizationServer:()=>({getOAuthApi:()=>({listUserGrants:async()=>{calls++;return {items:[],cursor:'more'};}})})});
 for(let i=0;i<2;i++){const data=await (await h.get()).json();assert.equal(data.apps_capped,true);assert.equal(data.r2_sync.capped,false);assert.equal(data.apps.length,1);}
 assert.equal(calls,10);
});

test('MD-8 heartbeat objects are ignored by the Worker',async()=>{
 const h=await harness();await writePolicy(h,'client');
 await h.env.STORAGE.put('registry/heartbeats/'+h.deviceId+'.json',bytes('DEVICE_NAME_SENTINEL'));
 const get=h.env.STORAGE.get.bind(h.env.STORAGE);
 h.env.STORAGE.get=async key=>{assert.ok(!key.startsWith('registry/heartbeats/'));return get(key);};
 const response=await h.get();assert.equal(response.status,200);
 assert.doesNotMatch(await response.text(),/DEVICE_NAME_SENTINEL/);
 const cached=await h.env.OAUTH_KV.get('status:inventory','json');
 assert.ok(!cached.objects.some(o=>o.key.startsWith('registry/heartbeats/')));
});
