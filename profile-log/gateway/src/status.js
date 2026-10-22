import { Buffer } from 'node:buffer';
import { bytes } from './encoding.js';
import { policy,secretWindow } from './registry.js';
import { tunnel } from './gateway.js';
import { ERROR_CODES,STATUS_APP,hour,statusShape,countError } from './status-metrics.js';
const headers={'Cache-Control':'no-store','Content-Security-Policy':"default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",'X-Frame-Options':'DENY','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff'};
export const statusResponse=(body,status=200,type='text/plain; charset=utf-8')=>new Response(body,{status,headers:{...headers,'Content-Type':type}});
const escape=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
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
  return typeof claims.email==='string'&&String(env.STATUS_ALLOWED_EMAILS??'').split(',').map(s=>s.trim().toLowerCase()).filter(Boolean).includes(claims.email.toLowerCase());
 }catch{return false;}};
}
const PREFIXES=['registry/','keyring/','keys/','events/','connectors/','outbox/'];
async function inventory(bucket) {
 const objects=[],counts=Object.fromEntries(PREFIXES.map(p=>[p,0]));let capped=false,pages=0;
 outer:for(const prefix of PREFIXES){let cursor;
  do{const page=await bucket.list({prefix,cursor,limit:Math.min(1000,10000-objects.length)});pages++;
   for(const o of page.objects){if(objects.length===10000){capped=true;break outer;}objects.push(o);counts[prefix]++;}
   cursor=page.truncated?page.cursor:undefined;
   if(objects.length>=10000||pages>=10000){capped=true;break outer;}
   if(page.truncated&&!cursor){capped=true;break outer;}
  }while(cursor);
 }
 return {objects,counts,capped};
}
function itemTime(o) {
 const id=/^outbox\/([0-7][0-9A-HJKMNP-TV-Z]{25})\.json$/.exec(o.key)?.[1];
 if(id){let n=0;for(const c of id.slice(0,10))n=n*32+'0123456789ABCDEFGHJKMNPQRSTVWXYZ'.indexOf(c);return n;}
 const at=new Date(o.uploaded).getTime();return Number.isFinite(at)?at:null;
}
async function devices(env,live) {
 const rows=[];let cursor,pages=0,capped=false;
 do{const page=await env.OAUTH_KV.list({prefix:'status:bridge:',cursor,limit:1000});
  for(const key of page.keys){if(rows.length===1000){capped=true;break;}try{const r=await env.OAUTH_KV.get(key.name,'json');if(r&&((r.device_id===null&&key.name==='status:bridge:unattributed')||(/^[0-9a-f]{32}$/.test(r.device_id)&&key.name==='status:bridge:'+r.device_id))&&Number.isSafeInteger(r.last_seen_ms)&&Number.isSafeInteger(r.latency_ms))rows.push({device_id:r.device_id,last_seen_ms:r.last_seen_ms,latency_ms:r.latency_ms,live:r.device_id!==null&&r.device_id===live?.device_id});}catch{}}
  cursor=page.list_complete?undefined:page.cursor;if(++pages>=10||rows.length>=1000){capped||=!!cursor;break;}
 }while(cursor);
 if(live){const row={device_id:live.device_id,last_seen_ms:Date.now(),latency_ms:live.latency_ms,live:true},index=rows.findIndex(r=>r.device_id===live.device_id);if(index<0)rows.push(row);else rows[index]=row;}
 return {rows,capped};
}
export async function statusData(env,api,fetcher) {
 const started=Date.now(),result=await tunnel(env,STATUS_APP,'status',{},fetcher);let live=null;
 try{if(!result.error)live={...statusShape(result),latency_ms:Date.now()-started};}catch{}
 const probe=live?{state:'online',...live}:{state:'offline',reason:result.error==='denied'?'denied':result.error==='agentmesh_offline'?'offline':'unavailable'};
 const inv=await inventory(env.STORAGE),at=Date.now(),outbox=inv.objects.filter(o=>o.key.startsWith('outbox/')),times=outbox.map(itemTime).filter(n=>n!==null);
 // Resolve controls through the bounded snapshot; never read data objects.
 const reads=new Map();
 const bounded={...env,STORAGE:{get:async key=>{if(!reads.has(key))reads.set(key,(async()=>{const object=await env.STORAGE.get(key);return object?await object.arrayBuffer():null;})());const data=await reads.get(key);return data?{arrayBuffer:async()=>data.slice(0)}:null;},list:async({prefix=''})=>({objects:inv.objects.filter(o=>o.key.startsWith(prefix)),truncated:false})}};
 const ids=[...new Set(inv.objects.map(o=>/^connectors\/([0-9a-f]{32})\/policy\//.exec(o.key)?.[1]).filter(Boolean))];
 const apps=[],windows=[];let appsCapped=inv.capped||ids.length>100;
 // A partial control inventory may omit revocations: do not resolve it as authoritative.
 if(!inv.capped)for(const app of ids.slice(0,100)){
  const p=await policy(bounded,app,at);if(!p)continue;const names=new Set();let cursor,has=false,pages=0;
  do{const page=await api.listUserGrants(app,{cursor,limit:100});const active=page.items.filter(g=>g.expiresAt===undefined||g.expiresAt>at/1000);has||=active.length>0;for(const grant of active){const client=await api.lookupClient(grant.clientId);if(typeof client?.clientName==='string')names.add(client.clientName.slice(0,256));}cursor=page.cursor;if(++pages===10&&cursor){appsCapped=true;break;}}while(cursor);
  apps.push({app_id:p.app_id,state:p.state,max_label:p.max_label,read:p.read,remember:p.remember,limits:p.limits,has_oauth_grants:has,client_names:[...names],issued_at:p.issued_at});
  const w=await secretWindow(bounded,app,at);if(w)windows.push({app_id:app,not_after:w.not_after});
 }
 const errors=Object.fromEntries(ERROR_CODES.map(c=>[c,0]));
 for(const code of ERROR_CODES)for(let i=0;i<24;i++){const n=Number(await env.OAUTH_KV.get(`status:err:${code}:${hour(at-i*3600000)}`)??0);if(Number.isSafeInteger(n)&&n>=0)errors[code]+=n;}
 const known=await devices(env,live),uploaded=inv.objects.filter(o=>o.key.startsWith('events/')).map(o=>new Date(o.uploaded).getTime()).filter(Number.isFinite);
 return {generated_at_ms:at,bridges:{probe,devices:known.rows,capped:known.capped,note:'The Tunnel routes to any healthy connector; this is observed history, not device enumeration. Unattributed responses cannot identify a device.'},outbox:{count:outbox.length,oldest_age_ms:times.length?Math.max(0,at-Math.min(...times)):null,newest_age_ms:times.length?Math.max(0,at-Math.max(...times)):null,unknown_age_count:outbox.length-times.length,capped:inv.capped,last_drain_ms:live?.last_drain_ms??null,last_drain_result:live?.last_drain_result??null,outbox_cursor_ms:live?.outbox_cursor_ms??null,note:'Object count is not a pending count: the Worker cannot determine which items were drained.'},apps,apps_capped:appsCapped,secret_windows:windows,gateway_errors:errors,r2_sync:{counts:inv.counts,latest_event_uploaded_ms:uploaded.length?Math.max(...uploaded):null,capped:inv.capped,object_limit:10000,note:'Counts and latest upload cover the scanned objects only; capped results are partial. Upload time does not prove convergence.'}};
}
export async function serveStatus(request,env,api,fetcher) {
 try{const data=await statusData(env,api,fetcher);if(new URL(request.url).pathname==='/status.json')return statusResponse(JSON.stringify(data),200,'application/json');
  const sections=[['Bridges',data.bridges],['Outbox',data.outbox],['Apps',{items:data.apps,capped:data.apps_capped}],['Secret windows',data.secret_windows],['Gateway errors',data.gateway_errors],['R2 sync',data.r2_sync]];
  return statusResponse('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>AgentMesh status</title><body><h1>AgentMesh status</h1><p>Read-only metadata. Reload for one live bridge probe. Error counts are approximate, over the current and previous 23 UTC hours.</p>'+sections.map(([title,value])=>`<section><h2>${title}</h2><pre>${escape(JSON.stringify(value,null,2))}</pre></section>`).join('')+'</body></html>',200,'text/html; charset=utf-8');
 }catch{countError(env,'unavailable');return statusResponse('Unavailable',503);}
}
