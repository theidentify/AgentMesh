import { namespaced } from '../src/storage.js';
import { Buffer } from 'node:buffer';
import { bytes,wire,hex,sha,sign } from '../src/encoding.js';
import { gatewayId } from '../src/registry.js';
import { ulid } from '../src/gateway.js';
export class FakeR2 {
 constructor(){this.data=new Map();}
 async put(key,value,options={}) {if(options.onlyIf&&this.data.has(key))return null;this.data.set(key,new Uint8Array(value));return {key,etag:'fake'};}
 async get(key){const data=this.data.get(key);return data?{arrayBuffer:async()=>data.slice().buffer}:null;}
 async list({prefix='',cursor}={}){const all=[...this.data.keys()].filter(k=>k.startsWith(prefix)).sort(),start=Number(cursor??0),selected=all.slice(start,start+2);return {objects:selected.map(key=>({key})),truncated:start+2<all.length,cursor:String(start+2)};}
}
export class FakeKV {
 constructor(){this.data=new Map();}
 async get(key,type){const row=this.data.get(key);if(!row||row.expires<=Date.now())return null;return (typeof type==='string'?type:type?.type)==='json'?JSON.parse(row.value):row.value;}
 async put(key,value,opts={}){this.data.set(key,{value,expires:opts.expiration?opts.expiration*1000:Date.now()+(opts.expirationTtl??1e9)*1000});}
 async delete(key){this.data.delete(key);}
 async list({prefix='',cursor,limit=1000}={}){const keys=[...this.data.keys()].filter(k=>k.startsWith(prefix)).sort();return {keys:keys.map(name=>({name})),list_complete:true,cursor:''};}
}
export async function makeEnv() {
 const root=await crypto.subtle.generateKey('Ed25519',true,['sign','verify']),device=await crypto.subtle.generateKey('Ed25519',true,['sign','verify']),gateway=await crypto.subtle.generateKey('Ed25519',true,['sign','verify']);
 const pub=async k=>hex(await crypto.subtle.exportKey('raw',k.publicKey));
 const account={format:'agentmesh-account-v1',account_id:'01'.repeat(16),root_public_key:await pub(root)},id='02'.repeat(16),issued_at=Date.now()-10000;
 const env={ORIGIN:'https://gateway.example',BRIDGE_ORIGIN:'https://bridge.example',ACCOUNT_PUBLIC_RECORD:JSON.stringify(account),STORAGE:new FakeR2(),OAUTH_KV:new FakeKV(),RATE_LIMITER:{limit:async()=>({success:true})},ACCESS_CLIENT_ID:'synthetic-id',ACCESS_CLIENT_SECRET:'synthetic-credential',GATEWAY_SIGNING_KEY:Buffer.from(await crypto.subtle.exportKey('pkcs8',gateway.privateKey)).toString('base64')};
 const cert=await sign({format:'agentmesh-device-cert-v1',account_id:account.account_id,device_id:id,device_public_key:await pub(device),issuer:'account',issued_at},root.privateKey,'device-cert');
 await env.STORAGE.put(`registry/devices/${id}.json`,wire(await sign({format:'agentmesh-device-record-v1',certificate:cert,age_recipient:'age1synthetic',issued_at},device.privateKey,'device-record')));
 const pin=await sign({format:'agentmesh-gateway-v1',gateway_public_key:await pub(gateway),origin:env.ORIGIN,issued_at,issuer_device:id},device.privateKey,'gateway');env.GATEWAY_KEY_ID=await gatewayId(pin);await env.STORAGE.put(`registry/gateway/${env.GATEWAY_KEY_ID}.json`,wire(pin));
 const outbox=await crypto.subtle.generateKey('X25519',true,['deriveBits']);
 await env.STORAGE.put(`registry/outbox-keys/${id}.json`,wire(await sign({format:'agentmesh-outbox-key-v1',device_id:id,outbox_public_key:await pub(outbox),issued_at},device.privateKey,'outbox-key')));
 return {env,device:device.privateKey,deviceId:id,root,outbox};
}
export async function admin(h,action,payload){const r=await sign({format:'agentmesh-connector-admin-v1',device_id:h.deviceId,ts:Date.now(),nonce:hex(crypto.getRandomValues(new Uint8Array(16))),action,payload},h.device,'connector-admin');return h.worker.fetch(new Request(h.env.ORIGIN+'/admin/'+action,{method:'POST',body:wire(r)}),h.env);}
export async function writePolicy(h,client,changes={}){
 const p=await sign({format:'agentmesh-connector-policy-v1',app_id:hex(crypto.getRandomValues(new Uint8Array(16))),display_name_hash:await sha(bytes('Claude')),oauth_client_id_hash:await sha(bytes(client)),read:[{profile:'03'.repeat(16),scopes:null}],remember:{profile:'03'.repeat(16),scope:'04'.repeat(16)},max_label:'internal',allow_cross_profile:false,limits:{recall_limit_max:3,requests_per_minute:60},state:'active',issuer_device:h.deviceId,issued_at:Date.now(),...changes},h.device,'connector-policy');
 const ref=`connectors/${p.app_id}/policy/${ulid()}.json`;await namespaced(h.env).STORAGE.put(ref,wire(p));return {p,ref};
}
export async function register(h){const r=await h.worker.fetch(new Request(h.env.ORIGIN+'/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({client_name:'Claude',redirect_uris:['https://client.example/callback'],grant_types:['authorization_code','refresh_token'],response_types:['code'],token_endpoint_auth_method:'none'})}),h.env);return r.json();}
export async function start(h,client,method='S256'){
 const verifier='a'.repeat(43),challenge=Buffer.from(await crypto.subtle.digest('SHA-256',bytes(verifier))).toString('base64url');
 const query=new URLSearchParams({client_id:client,response_type:'code',redirect_uri:'https://client.example/callback',scope:'memory',state:'synthetic-state',resource:h.env.ORIGIN+'/mcp'});
 if(method!==null){query.set('code_challenge',challenge);query.set('code_challenge_method',method);}
 const response=await h.worker.fetch(new Request(h.env.ORIGIN+'/authorize?'+query),h.env);const text=await response.text();
 return {response,code: /<strong>([0-9]{6})<\/strong>/.exec(text)?.[1],cookie:response.headers.get('Set-Cookie')?.split(';')[0],verifier};
}
export async function finish(h,s){return h.worker.fetch(new Request(h.env.ORIGIN+'/authorize/complete',{method:'POST',headers:{Cookie:s.cookie}}),h.env);}
export async function exchange(h,client,code,verifier){return h.worker.fetch(new Request(h.env.ORIGIN+'/token',{method:'POST',body:new URLSearchParams({grant_type:'authorization_code',client_id:client,code,code_verifier:verifier,redirect_uri:'https://client.example/callback',resource:h.env.ORIGIN+'/mcp'})}),h.env);}
export async function approved(h){const c=await register(h);if(!c.client_id)throw Error('registration_failed');const s=await start(h,c.client_id);if(!s.code)throw Error('authorization_failed');const {p,ref}=await writePolicy(h,c.client_id);const r=await admin(h,'approve',{code_hash:await sha(bytes(s.code)),app_id:p.app_id,policy_ref:ref});if(r.status!==200)throw Error('approval_failed');const f=await finish(h,s);if(f.status!==302)throw Error('completion_failed');const code=new URL(f.headers.get('Location')).searchParams.get('code');const token=await (await exchange(h,c.client_id,code,s.verifier)).json();if(!token.access_token)throw Error('exchange_failed');return {client:c.client_id,p,ref,token,s};}
export async function rpc(h,token,method,params={}){return h.worker.fetch(new Request(h.env.ORIGIN+'/mcp',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json',Accept:'application/json, text/event-stream','MCP-Protocol-Version':'2025-03-26'},body:JSON.stringify({jsonrpc:'2.0',id:1,method,params})}),h.env);}
