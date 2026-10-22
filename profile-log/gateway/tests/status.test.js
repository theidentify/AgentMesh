import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { Buffer } from 'node:buffer';
import { makeEnv,writePolicy,approved } from './fakes.mjs';
import { bytes,wire,sign } from '../src/encoding.js';
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
 for(const title of ['Bridges','Outbox','Apps','Secret windows','Gateway errors','R2 sync'])assert.ok(text.includes(title));
 assert.doesNotMatch(text,/ciphertext-marker|remembered-body-marker/);assert.equal(h.probes,2);assert.equal(h.jwks,1);
 for(const r of [response,html]){assert.equal(r.headers.get('Cache-Control'),'no-store');assert.equal(r.headers.get('X-Frame-Options'),'DENY');assert.equal(r.headers.get('Referrer-Policy'),'no-referrer');assert.equal(r.headers.get('Access-Control-Allow-Origin'),null);assert.match(r.headers.get('Content-Security-Policy'),/default-src 'none'/);}
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
 for(const path of ['/','/mcp','/authorize','/token','/register','/admin/pending','/.well-known/oauth-authorization-server','/.well-known/oauth-protected-resource'])for(const method of ['GET','POST','OPTIONS']){const r=await h.get(path,await jwt(),h.env.STATUS_ORIGIN,method);assert.equal(r.status,404);assert.equal(r.headers.get('Access-Control-Allow-Origin'),null);}
 assert.equal((await h.get('/status',await jwt(),h.env.STATUS_ORIGIN,'POST')).status,404);
 delete h.env.STATUS_ORIGIN;assert.equal((await h.get('/status',await jwt(),'https://status.example')).status,404);
});
test('P6-S4 denied tool and HTTP errors increment bounded hourly counters',async()=>{
 const h=await harness();assert.deepEqual(await callTool(h.env,{props:{app_id:'ab'.repeat(16)},clientId:'missing'},'whoami',{}),{error:'denied'});
 await h.worker.fetch(new Request(h.env.ORIGIN+'/mcp'),h.env);
 const data=await (await h.get()).json();assert.equal(data.gateway_errors.denied,1);assert.equal(data.gateway_errors.invalid_token,1);
 for(const [key,row]of h.env.OAUTH_KV.data)if(key.startsWith('status:err:')){assert.match(key,/status:err:[a-z_]+:\d{10}$/);assert.ok(row.expires<=Date.now()+172800000);}
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
