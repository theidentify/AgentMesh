import { Buffer } from 'node:buffer';
import { bytes,hex,sha,unhex,exact } from './encoding.js';
import { registry,policy,secretWindow } from './registry.js';
import { tunnel,claim } from './gateway.js';
import { STATUS_APP,statusShape,countError,errorCounts } from './status-metrics.js';
import { escape,statusPage,statusDashboard } from './status-ui.js';
const headers={'Cache-Control':'no-store','Content-Security-Policy':"default-src 'none'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",'X-Frame-Options':'DENY','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff'};
export const statusResponse=(body,status=200,type='text/plain; charset=utf-8')=>new Response(body,{status,headers:{...headers,'Content-Type':type}});
// Each gateway instance caches only public signing keys, never assertions or claims.
export function accessVerifier(fetcher) {
 let cache={issuer:null,until:0,keys:[]};
 return async(request,env)=>{try{
  const team=env.ACCESS_TEAM_DOMAIN;if(typeof team!=='string'||!/^([a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$/i.test(team)||!env.STATUS_ACCESS_AUD)return false;
  const issuer='https://'+team,token=request.headers.get('Cf-Access-Jwt-Assertion');if(!token||token.length>16384)return false;
  const parts=token.split('.');if(parts.length!==3||parts.some(s=>!s||!/^[A-Za-z0-9_-]+$/.test(s)))return false;
  const head=JSON.parse(Buffer.from(parts[0],'base64url')),claims=JSON.parse(Buffer.from(parts[1],'base64url'));
  if(head.alg!=='RS256'||typeof head.kid!=='string'||!head.kid||head.crit!==undefined)return false;
  async function refresh(){
   const controller=new AbortController();let timer;
   try{const keys=await Promise.race([(async()=>{const r=await fetcher(issuer+'/cdn-cgi/access/certs',{redirect:'manual',signal:controller.signal});if(!r.ok)throw Error();const data=await r.json();if(!Array.isArray(data.keys)||data.keys.length>100)throw Error();return data.keys;})(),new Promise((_,reject)=>{timer=setTimeout(()=>{controller.abort();reject(Error());},3000);})]);cache={issuer,until:Date.now()+300000,keys};}finally{clearTimeout(timer);}
  }
  const fresh=cache.issuer!==issuer||cache.until<=Date.now();if(fresh)await refresh();
  let key=cache.keys.find(k=>k.kid===head.kid);if(!key&&!fresh){await refresh();key=cache.keys.find(k=>k.kid===head.kid);}
  if(!key||key.kty!=='RSA'||(key.alg&&key.alg!=='RS256')||(key.use&&key.use!=='sig'))return false;
  const publicKey=await crypto.subtle.importKey('jwk',key,{name:'RSASSA-PKCS1-v1_5',hash:'SHA-256'},false,['verify']);
  if(!await crypto.subtle.verify('RSASSA-PKCS1-v1_5',publicKey,Buffer.from(parts[2],'base64url'),bytes(parts[0]+'.'+parts[1])))return false;
  const now=Date.now()/1000,skew=30,aud=Array.isArray(claims.aud)?claims.aud:[claims.aud];
  if(claims.iss!==issuer||!aud.includes(env.STATUS_ACCESS_AUD)||!Number.isSafeInteger(claims.exp)||!Number.isSafeInteger(claims.iat)||claims.exp<=now-skew||claims.iat>now+skew||claims.iat>=claims.exp)return false;
  if(claims.nbf!==undefined&&(!Number.isSafeInteger(claims.nbf)||claims.nbf>now+skew||claims.nbf>=claims.exp))return false;
  if(!(typeof claims.email==='string'&&String(env.STATUS_ALLOWED_EMAILS??'').split(',').map(s=>s.trim().toLowerCase()).filter(Boolean).includes(claims.email.toLowerCase())))return false;
  return {sub:claims.sub,email:claims.email};
 }catch{return false;}};
}
const PREFIXES=['registry/','keyring/','keys/','events/','connectors/','outbox/'];
async function inventory(bucket) {
 const scans=await Promise.all(PREFIXES.map(async prefix=>{
  const objects=[];let cursor,pages=0,capped=false;
  do{const page=await bucket.list({prefix,cursor,limit:Math.min(1000,10000-objects.length)});
   objects.push(...page.objects.slice(0,10000-objects.length).map(o=>({key:o.key,...(o.uploaded!==undefined?{uploaded:o.uploaded}:{})})));cursor=page.truncated?page.cursor:undefined;
   if(objects.length>=10000||++pages>=10000||(page.truncated&&!cursor)){capped=true;break;}
  }while(cursor);return {objects,capped};
 }));
 const objects=[],counts=Object.fromEntries(PREFIXES.map(p=>[p,0]));let capped=false;
 for(const [i,scan]of scans.entries()){
  const selected=scan.objects.slice(0,10000-objects.length);objects.push(...selected);counts[PREFIXES[i]]=selected.length;
  capped||=scan.capped||selected.length<scan.objects.length||objects.length===10000;
 }
 return {objects,counts,capped};
}
async function inventorySnapshot(env) {
 const at=Date.now();
 try{const cached=await env.OAUTH_KV.get('status:inventory','json');if(cached&&Number.isSafeInteger(cached.generated_at_ms)&&at>=cached.generated_at_ms&&at-cached.generated_at_ms<45000&&Array.isArray(cached.objects)&&cached.objects.length<=10000&&typeof cached.capped==='boolean'&&cached.counts&&Array.isArray(cached.apps)&&cached.apps.length<=100&&Array.isArray(cached.secret_windows)&&cached.secret_windows.length<=100&&typeof cached.apps_capped==='boolean')return cached;}catch{}
 return {...await inventory(env.STORAGE),generated_at_ms:at};
}
async function timedStage(timings,name,operation) {
 const started=Date.now();try{return await operation();}finally{timings[name]=Date.now()-started;}
}

function itemTime(o) {
 const id=/^outbox\/([0-7][0-9A-HJKMNP-TV-Z]{25})\.json$/.exec(o.key)?.[1];
 if(id){let n=0;for(const c of id.slice(0,10))n=n*32+'0123456789ABCDEFGHJKMNPQRSTVWXYZ'.indexOf(c);return n;}
 const at=new Date(o.uploaded).getTime();return Number.isFinite(at)?at:null;
}
async function devices(env) {
 const rows=[];let cursor,pages=0,capped=false;
 do{const page=await env.OAUTH_KV.list({prefix:'status:bridge:',cursor,limit:1000});
  for(const key of page.keys){if(rows.length===1000){capped=true;break;}try{const r=await env.OAUTH_KV.get(key.name,'json');if(r&&((r.device_id===null&&key.name==='status:bridge:unattributed')||(/^[0-9a-f]{32}$/.test(r.device_id)&&key.name==='status:bridge:'+r.device_id))&&Number.isSafeInteger(r.last_seen_ms)&&Number.isSafeInteger(r.latency_ms))rows.push({device_id:r.device_id,last_seen_ms:r.last_seen_ms,latency_ms:r.latency_ms,live:false});}catch{}}
  cursor=page.list_complete?undefined:page.cursor;if(++pages>=10||rows.length>=1000){capped||=!!cursor;break;}
 }while(cursor);
 return {rows,capped};
}
export async function statusData(env,api,fetcher,timings={}) {
 const started=Date.now();
 const [result,inv,errors,known]=await Promise.all([
  timedStage(timings,'probe',()=>tunnel(env,STATUS_APP,'status',{},fetcher)),
  timedStage(timings,'inventory',()=>inventorySnapshot(env)),
  timedStage(timings,'errors',()=>errorCounts(env,started)),
  timedStage(timings,'devices',()=>devices(env))
 ]);
 let live=null;try{if(!result.error)live={...statusShape(result),latency_ms:timings.probe};}catch{}
 const at=Date.now(),probe=live?{state:'online',...live}:{state:'offline',reason:result.error==='denied'?'denied':result.error==='agentmesh_offline'?'offline':'unavailable'};
 if(live){const row={device_id:live.device_id,last_seen_ms:at,latency_ms:live.latency_ms,live:true},index=known.rows.findIndex(r=>r.device_id===live.device_id);if(index<0)known.rows.push(row);else known.rows[index]=row;}
 const outbox=inv.objects.filter(o=>o.key.startsWith('outbox/')),times=outbox.map(itemTime).filter(n=>n!==null);
 const {apps,apps_capped:appsCapped,secret_windows:windows}=await timedStage(timings,'apps',async()=>{
  if(!inv.apps){
   // Resolve controls through the bounded snapshot; never read data objects.
   const reads=new Map();
   const bounded={...env,STORAGE:{get:async key=>{if(!reads.has(key))reads.set(key,(async()=>{const object=await env.STORAGE.get(key);return object?await object.arrayBuffer():null;})());const data=await reads.get(key);return data?{arrayBuffer:async()=>data.slice(0)}:null;},list:async({prefix=''})=>({objects:inv.objects.filter(o=>o.key.startsWith(prefix)),truncated:false})}};
   const ids=[...new Set(inv.objects.map(o=>/^connectors\/([0-9a-f]{32})\/policy\//.exec(o.key)?.[1]).filter(Boolean))];
   const apps=[],windows=[];let appsCapped=inv.capped||ids.length>100;
   // A partial control inventory may omit revocations: do not resolve it as authoritative.
   if(!inv.capped&&ids.length){
    const reg=await registry(bounded);
    const rows=await Promise.all(ids.slice(0,100).map(async app=>{
     const [p,w]=await Promise.all([policy(bounded,app,at,reg),secretWindow(bounded,app,at,reg)]);if(!p)return null;
     const names=new Set();let cursor,has=false,pages=0;
     do{const page=await api.listUserGrants(app,{cursor,limit:100}),active=page.items.filter(g=>g.expiresAt===undefined||g.expiresAt>at/1000);has||=active.length>0;
      const clients=await Promise.all(active.map(grant=>api.lookupClient(grant.clientId)));for(const client of clients)if(typeof client?.clientName==='string')names.add(client.clientName.slice(0,256));
      cursor=page.cursor;if(++pages===10&&cursor){appsCapped=true;break;}
     }while(cursor);
     return {app:{app_id:p.app_id,state:p.state,max_label:p.max_label,read:p.read,remember:p.remember,limits:p.limits,has_oauth_grants:has,client_names:[...names],issued_at:p.issued_at},window:w?{app_id:app,not_before:w.not_before,not_after:w.not_after}:null};
    }));
    for(const row of rows)if(row){apps.push(row.app);if(row.window)windows.push(row.window);}
   }
   // Publish inventory and resolved metadata together; actions delete this one entry.
   Object.assign(inv,{apps,secret_windows:windows,apps_capped:appsCapped});
   try{await env.OAUTH_KV.put('status:inventory',JSON.stringify(inv),{expirationTtl:60});}catch{}
  }
  const now=Date.now();
  return {apps:inv.capped?[]:inv.apps,apps_capped:inv.capped||inv.apps_capped,secret_windows:inv.capped?[]:inv.secret_windows.filter(w=>w.not_before<=now&&now<w.not_after).map(({app_id,not_after})=>({app_id,not_after}))};
 });
 const uploaded=inv.objects.filter(o=>o.key.startsWith('events/')).map(o=>new Date(o.uploaded).getTime()).filter(Number.isFinite);
 return {generated_at_ms:at,bridges:{probe,devices:known.rows,capped:known.capped,note:'The Tunnel routes to any healthy connector; this is observed history, not device enumeration. Unattributed responses cannot identify a device.'},outbox:{count:outbox.length,oldest_age_ms:times.length?Math.max(0,at-Math.min(...times)):null,newest_age_ms:times.length?Math.max(0,at-Math.max(...times)):null,unknown_age_count:outbox.length-times.length,capped:inv.capped,last_drain_ms:live?.last_drain_ms??null,last_drain_result:live?.last_drain_result??null,outbox_cursor_ms:live?.outbox_cursor_ms??null,note:'Object count is not a pending count: the Worker cannot determine which items were drained.'},apps,apps_capped:appsCapped,secret_windows:windows,gateway_errors:errors,r2_sync:{counts:inv.counts,latest_event_uploaded_ms:uploaded.length?Math.max(...uploaded):null,capped:inv.capped,object_limit:10000,note:'Counts and latest upload cover the scanned objects only; capped results are partial. Upload time does not prove convergence.'}};
}
async function pendingRequests(env) {
 const rows=[];let cursor,pages=0,capped=false;
 do{const page=await env.OAUTH_KV.list({prefix:'pending:',cursor,limit:100});
  for(const key of page.keys){if(rows.length>=100){capped=true;break;}const hash=key.name.slice(8);if(!/^[0-9a-f]{64}$/.test(hash))continue;
   const p=await env.OAUTH_KV.get(key.name,'json');if(p&&!p.approval&&!p.completed&&!p.rejected&&p.expires>Date.now())rows.push({code_hash:hash,client_name:typeof p.client_name==='string'?p.client_name.slice(0,256):'App',expires:p.expires});
  }
  cursor=page.list_complete?undefined:page.cursor;if(++pages>=10||rows.length>=100){capped||=!!cursor;break;}
 }while(cursor);return {rows,capped};
}
export async function statusAction(request,env,identity,applyAdmin,fetcher) {
 try{
  if(request.headers.get('Origin')!==env.STATUS_ORIGIN||![null,'same-origin'].includes(request.headers.get('Sec-Fetch-Site')))throw Error();
  if(typeof identity.sub!=='string'||!identity.sub||Number(request.headers.get('Content-Length')??0)>4096)throw Error();
  if(!request.headers.get('Content-Type')?.startsWith('application/x-www-form-urlencoded'))throw Error();
  const raw=await request.text();if(bytes(raw).length>4096)throw Error();
  const form=new URLSearchParams(raw),body=Object.fromEntries(form);if([...form].length!==Object.keys(body).length)throw Error();
  const {csrf,action,...args}=body,field=action==='reject'?'code_hash':'app_id';
  if(!['reject','revoke','close_secret'].includes(action))throw Error();exact(args,[field]);unhex(args[field],action==='reject'?32:16);unhex(csrf,32);
  const hash=await sha(bytes(csrf)),key='status:csrf:'+hash,stored=await env.OAUTH_KV.get(key,'json');
  if(!stored||stored.sub!==identity.sub||stored.email!==identity.email||!Number.isSafeInteger(stored.exp)||stored.exp<=Date.now())throw Error();
  if(!await claim(env,'status-csrf/'+hash))throw Error();await env.OAUTH_KV.delete(key);
  const result=await tunnel(env,STATUS_APP,'admin',{action,...args},fetcher);
  if(result.error)throw Error();
  if(action==='close_secret'){exact(result,['status']);if(result.status!=='ok')throw Error();}
  else{
   if(result.action!==action||result.payload?.[field]!==args[field])throw Error();
   const response=await applyAdmin(result,action);if(!response.ok)throw Error();
  }
  await env.OAUTH_KV.delete('status:inventory');
  return statusResponse(statusPage('Action completed','<section class="panel"><h2>Action completed.</h2><a class="button" href="/status">Return to status</a></section>'),200,'text/html; charset=utf-8');
 }catch{return statusResponse('Forbidden',403);}
}
export async function serveStatus(request,env,api,fetcher,identity) {
 const started=Date.now(),timings={probe:0,inventory:0,apps:0,errors:0,devices:0,pending:0,render:0,total:0};
 const timed=response=>{timings.total=Date.now()-started;response.headers.set('Server-Timing',Object.entries(timings).map(([name,duration])=>`${name};dur=${duration}`).join(', '));return response;};
 try{const json=new URL(request.url).pathname==='/status.json';
  const [data,pending]=await Promise.all([statusData(env,api,fetcher,timings),json?null:timedStage(timings,'pending',()=>pendingRequests(env))]);if(json)return timed(statusResponse(JSON.stringify(data),200,'application/json'));
  let stage,form=()=>'';
  if(typeof identity?.sub==='string'&&identity.sub){
   const token=hex(crypto.getRandomValues(new Uint8Array(32)));
   await env.OAUTH_KV.put('status:csrf:'+await sha(bytes(token)),JSON.stringify({...identity,exp:Date.now()+900000}),{expirationTtl:900});
   form=(action,field,value,label)=>{
    const html=`<form method="post" action="/status/action"><input type="hidden" name="csrf" value="${token}"><input type="hidden" name="action" value="${action}"><input type="hidden" name="${field}" value="${escape(value)}"><button class="danger">${escape(action==='revoke'?'Confirm revoke':label)}</button></form>`;
    return action==='revoke'?'<details><summary class="danger">Revoke</summary><p>Revoke access for this app?</p>'+html+'</details>':html;
   };
  }
  stage=Date.now();const html=statusDashboard(data,pending,form);timings.render=Date.now()-stage;
  return timed(statusResponse(html,200,'text/html; charset=utf-8'));
 }catch{countError(env,'unavailable');return timed(statusResponse('Unavailable',503));}
}
