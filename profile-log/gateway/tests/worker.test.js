import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile,readdir } from 'node:fs/promises';
import { Buffer } from 'node:buffer';
import { makeEnv,register,start,finish,exchange,approved,admin,writePolicy,rpc } from './fakes.mjs';
import { bytes,sha,wire } from '../src/encoding.js';
import { callTool,PREAMBLE } from '../src/gateway.js';
const {createGateway}=await import('../src/worker.js');
async function harness(){const h=await makeEnv();h.worker=createGateway({fetcher:async()=>new Response(null,{status:502})});return h;}
test('P6-1 no data-key/decrypt imports in Worker source',async()=>{
 const dir=new URL('../src/',import.meta.url);for(const name of await readdir(dir)){const src=await readFile(new URL(name,dir),'utf8');assert.doesNotMatch(src,/\b(?:ScopeReadKey|age_identity|scope_private|createRecipientContext|\.open\(|\.decrypt\()/);}
});
test('P6-2 registration alone, missing/plain PKCE, pending and wrong code grant nothing',async()=>{
 const h=await harness(),c=await register(h);assert.ok(c.client_id);
 assert.equal((await rpc(h,c.client_id,'tools/list')).status,401);
 for(const method of [null,'plain'])assert.equal((await start(h,c.client_id,method)).response.status,403);
 const s=await start(h,c.client_id);assert.ok(s.code);assert.equal((await finish(h,s)).status,409);
 const {p,ref}=await writePolicy(h,c.client_id);
 assert.equal((await admin(h,'approve',{code_hash:'00'.repeat(32),app_id:p.app_id,policy_ref:ref})).status,403);
 const hash=await sha(bytes(s.code));const pending=await h.env.OAUTH_KV.get('pending:'+hash,'json');pending.expires=Date.now()-1;await h.env.OAUTH_KV.put('pending:'+hash,JSON.stringify(pending));
 assert.equal((await admin(h,'approve',{code_hash:hash,app_id:p.app_id,policy_ref:ref})).status,403);
});
test('P6-2 approved OAuth, single-use approval, hashed rotating tokens, immediate revoke',async()=>{
 const h=await harness(),a=await approved(h);assert.ok(a.token.access_token);
 const kv=JSON.stringify([...h.env.OAUTH_KV.data]);assert.ok(!kv.includes(a.token.access_token));assert.ok(!kv.includes(a.token.refresh_token));
 assert.equal((await admin(h,'approve',{code_hash:await sha(bytes(a.s.code)),app_id:a.p.app_id,policy_ref:a.ref})).status,403);
 assert.equal((await finish(h,a.s)).status,403);
 const r=await h.worker.fetch(new Request(h.env.ORIGIN+'/token',{method:'POST',body:new URLSearchParams({grant_type:'refresh_token',client_id:a.client,refresh_token:a.token.refresh_token})}),h.env);const next=await r.json();assert.ok(next.refresh_token);assert.notEqual(next.refresh_token,a.token.refresh_token);
 const {ref}=await writePolicy(h,a.client,{...a.p,state:'revoked',issued_at:Date.now()+1});await new Promise(r=>setTimeout(r,5));
 assert.equal((await rpc(h,a.token.access_token,'tools/list')).status,403);
 assert.equal((await admin(h,'revoke',{app_id:a.p.app_id,policy_ref:ref})).status,200);
 assert.equal((await rpc(h,a.token.access_token,'tools/list')).status,401);
});
test('P6-8 only three tools, strict schemas, limit cap, fixed errors and preamble',async()=>{
 const h=await harness(),a=await approved(h);let request;
 h.worker=createGateway({fetcher:async(url,options)=>{request=JSON.parse(new TextDecoder().decode(options.body));return Response.json({data:{results:[],candidate_counts:{durable:0}}});}});
 const listing=await (await rpc(h,a.token.access_token,'tools/list')).json();assert.deepEqual(listing.result.tools.map(t=>t.name).sort(),['recall','remember','whoami']);
 for(const method of ['resources/list','prompts/list','sampling/createMessage'])assert.deepEqual(await (await rpc(h,a.token.access_token,method)).json(),{error:'invalid_request'});
 const oversized=await (await rpc(h,a.token.access_token,'tools/call',{name:'remember',arguments:{body:'x'.repeat(32769)}})).json();assert.equal(oversized.error.message,'invalid_request');
 const good=await (await rpc(h,a.token.access_token,'tools/call',{name:'recall',arguments:{query:'hello',limit:20}})).json();assert.equal(request.arguments.limit,3);assert.equal(good.result.structuredContent.preamble,PREAMBLE);
 for(const args of [{query:'x',extra:'private-marker'},{query:'x',profile:['x','y']},{query:'x'.repeat(2049)}]){const bad=await (await rpc(h,a.token.access_token,'tools/call',{name:'recall',arguments:args})).json();assert.equal(bad.error.message,'invalid_request');assert.ok(!JSON.stringify(bad).includes('private-marker'));}
 const denied=await callTool(h.env,{props:{app_id:a.p.app_id},clientId:a.client},'recall',{query:'x',profile:'ff'.repeat(16)});assert.deepEqual(denied,{error:'denied'});
});
test('P6-6/P6-7 offline recall fixed code and encrypted queued remember',async()=>{
 const h=await harness(),a=await approved(h),auth={props:{app_id:a.p.app_id},clientId:a.client};
 const fetcher=async()=>new Response(null,{status:530});const before=Date.now();
 assert.deepEqual(await callTool(h.env,auth,'recall',{query:'hello'},fetcher),{error:'agentmesh_offline'});assert.ok(Date.now()-before<10000);
 const result=await callTool(h.env,auth,'remember',{body:'unique-private-marker'},fetcher);assert.equal(result.status,'queued');
 const objects=[...h.env.STORAGE.data].filter(([k])=>k.startsWith('outbox/'));assert.equal(objects.length,1);assert.ok(!Buffer.from(objects[0][1]).includes(Buffer.from('unique-private-marker')));
});
test('P6-8 Workers binding and signed policy both cap request rate',async()=>{
 const h=await harness(),a=await approved(h),auth={props:{app_id:a.p.app_id},clientId:a.client};
 await writePolicy(h,a.client,{...a.p,issued_at:Date.now()+1,limits:{recall_limit_max:1,requests_per_minute:1}});await new Promise(r=>setTimeout(r,5));
 assert.equal((await callTool(h.env,auth,'whoami',{})).app_id,a.p.app_id);
 assert.deepEqual(await callTool(h.env,auth,'whoami',{}),{error:'rate_limited'});
 h.env.RATE_LIMITER.limit=async()=>({success:false});assert.deepEqual(await callTool(h.env,auth,'whoami',{}),{error:'rate_limited'});
});

test('P6-2 wrong PKCE verifier and unapproved token exchange are refused',async()=>{
 const h=await harness(),c=await register(h),s=await start(h,c.client_id);
 assert.equal((await exchange(h,c.client_id,'unapproved',s.verifier)).status,400);
 const {p,ref}=await writePolicy(h,c.client_id);assert.equal((await admin(h,'approve',{code_hash:await sha(bytes(s.code)),app_id:p.app_id,policy_ref:ref})).status,200);
 const completed=await finish(h,s),code=new URL(completed.headers.get('Location')).searchParams.get('code');
 assert.equal((await exchange(h,c.client_id,code,'b'.repeat(43))).status,400);
});
test('P6-3 app tokens bind distinct app IDs even with a malicious argument',async()=>{
 const h=await harness(),a=await approved(h),b=await approved(h),seen=[];
 h.worker=createGateway({fetcher:async(url,options)=>{seen.push(options.headers['X-Agentmesh-App']);return Response.json({data:{results:[]}});}});
 for(const app of [a,b])assert.equal((await rpc(h,app.token.access_token,'tools/call',{name:'recall',arguments:{query:'x'}})).status,200);
 assert.deepEqual(seen,[a.p.app_id,b.p.app_id]);
 const r=await (await rpc(h,a.token.access_token,'tools/call',{name:'recall',arguments:{query:'x',app_id:b.p.app_id}})).json();assert.equal(r.error.message,'invalid_request');assert.equal(seen.length,2);
});
test('P6-6 outbox excludes revoked devices and only listed recipient can decrypt',async()=>{
 const { sign,hex,concat,typed }=await import('../src/encoding.js');const { enqueue }=await import('../src/gateway.js');
 const {CipherSuite,HkdfSha256}=await import('@hpke/core');const {DhkemX25519HkdfSha256}=await import('@hpke/dhkem-x25519');const {Chacha20Poly1305}=await import('@hpke/chacha20poly1305');
 const h=await harness(),a=await approved(h),second=await crypto.subtle.generateKey('Ed25519',true,['sign','verify']),id='77'.repeat(16),at=Date.now()-100;
 const cert=await sign({format:'agentmesh-device-cert-v1',account_id:JSON.parse(h.env.ACCOUNT_PUBLIC_RECORD).account_id,device_id:id,device_public_key:hex(await crypto.subtle.exportKey('raw',second.publicKey)),issuer:'account',issued_at:at},h.root.privateKey,'device-cert');
 await h.env.STORAGE.put(`registry/devices/${id}.json`,wire(await sign({format:'agentmesh-device-record-v1',certificate:cert,age_recipient:'age1synthetic',issued_at:at},second.privateKey,'device-record')));
 await h.env.STORAGE.put(`registry/outbox-keys/${id}.json`,wire(await sign({format:'agentmesh-outbox-key-v1',device_id:id,outbox_public_key:hex(await crypto.subtle.exportKey('raw',h.outbox.publicKey)),issued_at:at},second.privateKey,'outbox-key')));
 const before=await enqueue(h.env,a.p.app_id,{body:'marker'});const readItem=async r=>JSON.parse(Buffer.from(h.env.STORAGE.data.get(`outbox/${r.outbox_id}.json`)));
 assert.equal((await readItem(before)).recipients.length,2);
 await h.env.STORAGE.put(`registry/revocations/${id}.json`,wire(await sign({format:'agentmesh-device-revocation-v1',device_id:id,revoked_by:h.deviceId,revoked_at:Date.now(),reason_code:'removed'},h.device,'device-revocation')));
 const after=await enqueue(h.env,a.p.app_id,{body:'marker'}),item=await readItem(after);assert.deepEqual(item.recipients.map(r=>r.device_id),[h.deviceId]);
 const suite=new CipherSuite({kem:new DhkemX25519HkdfSha256(),kdf:new HkdfSha256(),aead:new Chacha20Poly1305()});
 const raw=Buffer.from(item.recipients[0].ciphertext,'base64url'),info=concat(bytes('AgentMesh/outbox/v1\0'),typed({outbox_id:item.outbox_id,app_id:a.p.app_id,device_id:h.deviceId}));
 const receiver=await suite.createRecipientContext({recipientKey:await suite.kem.importKey('raw',Buffer.from((await crypto.subtle.exportKey('jwk',h.outbox.privateKey)).d,'base64url'),true),enc:raw.subarray(0,32),info});assert.equal(JSON.parse(Buffer.from(await receiver.open(raw.subarray(32)))).body,'marker');
 const wrong=await suite.kem.generateKeyPair(),bad=await suite.createRecipientContext({recipientKey:wrong.privateKey,enc:raw.subarray(0,32),info});await assert.rejects(bad.open(raw.subarray(32)));
});
test('P6-7 timeout and healthy Tunnel connector failover',async()=>{
 const h=await harness(),a=await approved(h),auth={props:{app_id:a.p.app_id},clientId:a.client};
 const online=[true,true];let served=[];
 const router=async()=>{const target=online.findIndex(Boolean);if(target<0)return new Response(null,{status:502});served.push(target);return Response.json({data:{results:[{id:'same-memory'}]}});};
 for(const index of [0,1]){online[0]=online[1]=true;online[index]=false;assert.ok((await callTool(h.env,auth,'recall',{query:'x'},router)).data.results.length);}
 assert.deepEqual(served,[1,0]);online[0]=online[1]=false;assert.deepEqual(await callTool(h.env,auth,'recall',{query:'x'},router),{error:'agentmesh_offline'});
 const before=Date.now();const results=await Promise.all([
  callTool(h.env,auth,'recall',{query:'x'},()=>new Promise(()=>{})),
  callTool(h.env,auth,'recall',{query:'x'},async()=>({ok:true,status:200,json:()=>new Promise(()=>{})})),
 ]);for(const r of results)assert.deepEqual(r,{error:'agentmesh_offline'});assert.ok(Date.now()-before<10000);
});

test('P6-2 Worker independently refuses missing PKCE before pending state',async()=>{
 const h=await harness(),c=await register(h);
 // A permissive provider isolates the Worker's own gate from library validation.
 h.worker=createGateway({authorizationServer:()=>({getOAuthApi:()=>({
  parseAuthRequest:async()=>({clientId:c.client_id,redirectUri:'https://client.example/callback',scope:['memory']}),
  lookupClient:async()=>({clientName:'Claude'}),
 })})});
 const result=await start(h,c.client_id,null);
 assert.equal(result.response.status,403);assert.equal(result.code,undefined);
 assert.equal([...h.env.OAUTH_KV.data.keys()].filter(k=>k.startsWith('pending:')).length,0);
});

test('P6-8 policy counter uses bounded expiring KV and zero R2 writes',async()=>{
 const h=await harness(),a=await approved(h),auth={props:{app_id:a.p.app_id},clientId:a.client};
 let writes=0;const put=h.env.STORAGE.put.bind(h.env.STORAGE);h.env.STORAGE.put=async(...args)=>{writes++;return put(...args);};
 for(let i=0;i<4;i++)assert.equal((await callTool(h.env,auth,'whoami',{})).app_id,a.p.app_id);
 assert.equal(writes,0);
 const counters=[...h.env.OAUTH_KV.data].filter(([key])=>key.startsWith('rate:'));
 assert.equal(counters.length,1);assert.equal(counters[0][1].value,'4');
 assert.ok(counters[0][1].expires<=Date.now()+120000);
});
