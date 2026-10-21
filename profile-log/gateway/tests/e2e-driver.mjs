import './setup.mjs';
import { Buffer } from 'node:buffer';
import { createInterface } from 'node:readline';
import { mkdir,readFile,writeFile,readdir } from 'node:fs/promises';
import { join } from 'node:path';
import { FakeKV,register,start,finish,exchange,admin,writePolicy,rpc } from './fakes.mjs';
import { bytes,sha } from '../src/encoding.js';
import { tunnel } from '../src/gateway.js';
const {createGateway}=await import('../src/worker.js');
class DirectoryR2 {
 constructor(directory){this.directory=directory;}
 path(key){if(!/^[A-Za-z0-9_-][A-Za-z0-9_./-]*$/.test(key)||key.split('/').some(x=>!x||x==='.'||x==='..'))throw Error('invalid');return join(this.directory,...key.split('/'));}
 async get(key){try{const raw=await readFile(this.path(key));return {arrayBuffer:async()=>raw.buffer.slice(raw.byteOffset,raw.byteOffset+raw.length)};}catch(e){if(e.code==='ENOENT')return null;throw e;}}
 async put(key,value,options={}){const path=this.path(key);await mkdir(join(path,'..'),{recursive:true,mode:0o700});try{await writeFile(path,value,{flag:options.onlyIf?'wx':'w',mode:0o600});return {key};}catch(e){if(e.code==='EEXIST'&&options.onlyIf)return null;throw e;}}
 async list({prefix=''}){const out=[];const walk=async(dir,base='')=>{for(const entry of await readdir(dir,{withFileTypes:true})){if(entry.name.startsWith('.'))continue;const key=base+entry.name;if(entry.isDirectory())await walk(join(dir,entry.name),key+'/');else if(entry.isFile()&&key.startsWith(prefix))out.push({key});}};await walk(this.directory);return {objects:out.sort((a,b)=>a.key.localeCompare(b.key)),truncated:false};}
}
let h,app;let sequence=0;const waiting=new Map();
async function ipcFetch(url,options){
 const id=++sequence;
 return new Promise(resolve=>{
  waiting.set(id,result=>resolve(Response.json(result.body,{status:result.status})));
  process.stdout.write(JSON.stringify({bridge_request:{id,headers:options.headers,body:Buffer.from(options.body).toString('base64')}})+'\n');
 });
}
async function command(c){
 if(c.op==='init'){
  const device=await crypto.subtle.importKey('pkcs8',Buffer.concat([Buffer.from('302e020100300506032b657004220420','hex'),Buffer.from(c.device_key,'hex')]),'Ed25519',false,['sign']);
  const env={R2_PREFIX:c.prefix??'',ORIGIN:'https://gateway.example',BRIDGE_ORIGIN:c.bridge,ACCOUNT_PUBLIC_RECORD:JSON.stringify(c.account),STORAGE:new DirectoryR2(c.directory),OAUTH_KV:new FakeKV(),RATE_LIMITER:{limit:async()=>({success:true})},ACCESS_CLIENT_ID:'synthetic',ACCESS_CLIENT_SECRET:'synthetic',GATEWAY_KEY_ID:c.gateway_id,GATEWAY_SIGNING_KEY:Buffer.from(c.gateway_key,'hex').toString('base64')};
  h={env,device,deviceId:c.device_id,ipc:c.ipc,worker:createGateway(c.ipc?{fetcher:ipcFetch}:{})};
  const client=await register(h);const pending=await start(h,client.client_id);const {p,ref}=await writePolicy(h,client.client_id,{read:c.read,remember:c.remember});
  const approved=await admin(h,'approve',{code_hash:await sha(bytes(pending.code)),app_id:p.app_id,policy_ref:ref});if(approved.status!==200)throw Error('approval');
  const finishResponse=await finish(h,pending);const code=new URL(finishResponse.headers.get('Location')).searchParams.get('code');const token=await (await exchange(h,client.client_id,code,pending.verifier)).json();if(!token.access_token)throw Error('token');
  app={p,ref,token,client:client.client_id};return {app_id:p.app_id};
 }
 if(c.op==='route'){h.env.BRIDGE_ORIGIN=c.bridge;return {ok:true};}
 if(c.op==='tool'){const r=await rpc(h,app.token.access_token,'tools/call',{name:c.tool,arguments:c.arguments});const result=await r.json();return result.result?.structuredContent??{error:result.error?.message??result.error??'unavailable'};}
 if(c.op==='revoke'){const {ref}=await writePolicy(h,app.client,{...app.p,state:'revoked',issued_at:Date.now()});const r=await admin(h,'revoke',{app_id:app.p.app_id,policy_ref:ref});return {ok:r.status===200};}
 // This bypass exists ONLY in this test driver, never production Worker source.
 if(c.op==='worker_check_disabled')return tunnel(h.env,app.p.app_id,c.tool,c.arguments,h.ipc?ipcFetch:fetch);
 throw Error('invalid');
}
if(process.argv[1]?.endsWith('e2e-driver.mjs')){
 const input=createInterface({input:process.stdin,crlfDelay:Infinity});let chain=Promise.resolve();
 input.on('line',line=>{
  let c;try{c=JSON.parse(line);}catch{process.stdout.write('{"driver_error":true}\n');return;}
  if(c.op==='bridge_response'){const resolve=waiting.get(c.id);waiting.delete(c.id);resolve?.(c);return;}
  chain=chain.then(()=>command(c)).then(result=>process.stdout.write(JSON.stringify(result)+'\n')).catch(()=>process.stdout.write('{"driver_error":true}\n'));
 });
}
