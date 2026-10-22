import { parse,verify,sha,bytes,concat,unhex,exact } from './encoding.js';
export async function keys(bucket,prefix) {
  const out=[];let cursor;
  do {const page=await bucket.list({prefix,cursor});out.push(...page.objects.map(x=>x.key));cursor=page.truncated?page.cursor:undefined;}while(cursor);
  return out.sort();
}
export async function read(bucket,key) {const o=await bucket.get(key);if(!o)throw Error('denied');return parse(new Uint8Array(await o.arrayBuffer()));}
export async function records(bucket,prefix) {const out=[];for(const path of await keys(bucket,prefix)){try{out.push([path,await read(bucket,path)]);}catch{}}return out;}
export const gatewayId=record=>sha(concat(bytes('AgentMesh/gateway-key-id/v1\0'),unhex(record.gateway_public_key,32)));
export async function registry(env) {
  const root=typeof env.ACCOUNT_PUBLIC_RECORD==='string'?JSON.parse(env.ACCOUNT_PUBLIC_RECORD):env.ACCOUNT_PUBLIC_RECORD;
  exact(root,['format','account_id','root_public_key']);if(root.format!=='agentmesh-account-v1')throw Error('denied');unhex(root.account_id,16);unhex(root.root_public_key,32);
  const devices=new Map(), revoked=new Map(), recovery=new Map();
  const recoveries=await records(env.STORAGE,'registry/recovery/');
  for(let pass=0;pass<=recoveries.length;pass++)for(const [path,r]of recoveries){try{
    exact(r,['format','signature','account_id','recovery_signing_public','recovery_age_recipient','previous','issued_at']);
    if(r.format!=='agentmesh-recovery-record-v1'||r.account_id!==root.account_id)continue;
    const id=await sha(concat(bytes('AgentMesh/recovery-key-id/v1\0'),unhex(r.recovery_signing_public,32)));
    if(path!==`registry/recovery/${id}.json`)continue;
    const issuer=r.previous===null?root.root_public_key:recovery.get(r.previous)?.recovery_signing_public;if(!issuer)continue;
    await verify(r,issuer,'recovery-record');recovery.set(id,r);
  }catch{}}
  const rows=await records(env.STORAGE,'registry/devices/');
  async function publicKey(id,at=Date.now(),seen=new Set()) {
    if(seen.has(id))throw Error('denied');seen.add(id);
    const c=devices.get(id);if(!c||!Number.isSafeInteger(at)||at<c.issued_at||(revoked.has(id)&&at>=revoked.get(id)))throw Error('denied');
    if(!['account','recovery'].includes(c.issuer))await publicKey(c.issuer,c.issued_at,seen);
    return c.device_public_key;
  }
  for(let pass=0;pass<=rows.length;pass++)for(const [path,r] of rows) {try{
    exact(r,['format','signature','certificate','age_recipient','issued_at']);if(r.format!=='agentmesh-device-record-v1')continue;
    const c=r.certificate;exact(c,['format','account_id','device_id','device_public_key','issuer','issued_at','signature']);
    if(c.format!=='agentmesh-device-cert-v1'||c.account_id!==root.account_id||c.issuer===c.device_id||path!==`registry/devices/${c.device_id}.json`)continue;
    unhex(c.device_id,16);unhex(c.device_public_key,32);if(!Number.isSafeInteger(c.issued_at)||c.issued_at<0)continue;
    if(c.issuer==='recovery') {let ok=false;for(const master of recovery.values()){if(master.issued_at>c.issued_at)continue;try{await verify(c,master.recovery_signing_public,'device-cert');ok=true;break;}catch{}}if(!ok)continue;}
    else await verify(c,c.issuer==='account'?root.root_public_key:await publicKey(c.issuer,c.issued_at),'device-cert');
    await verify(r,c.device_public_key,'device-record');
    if(devices.has(c.device_id)&&devices.get(c.device_id).device_public_key!==c.device_public_key)continue;
    devices.set(c.device_id,c);
  }catch{}}
  const revocations=(await records(env.STORAGE,'registry/revocations/')).sort((a,b)=>a[1].revoked_at-b[1].revoked_at);
  for(const [path,r]of revocations){try{
    exact(r,['format','signature','device_id','revoked_by','revoked_at','reason_code']);
    if(r.format!=='agentmesh-device-revocation-v1'||path!==`registry/revocations/${r.device_id}.json`||r.revoked_by===r.device_id||!['removed','recovery','compromised'].includes(r.reason_code))continue;
    if(r.revoked_by==='recovery'){let ok=false;for(const m of recovery.values()){if(m.issued_at>r.revoked_at)continue;try{await verify(r,m.recovery_signing_public,'device-revocation');ok=true;break;}catch{}}if(!ok)continue;}
    else await verify(r,await publicKey(r.revoked_by,r.revoked_at),'device-revocation');
    if(!devices.has(r.device_id))continue;revoked.set(r.device_id,r.revoked_at);
  }catch{}}
  return {publicKey,root};
}
export function policyShape(p) {
  exact(p,['format','signature','app_id','display_name_hash','oauth_client_id_hash','read','remember','max_label','allow_cross_profile','limits','state','issuer_device','issued_at']);
  if(p.format!=='agentmesh-connector-policy-v1'||!['general','internal'].includes(p.max_label)||p.allow_cross_profile!==false||!['active','revoked'].includes(p.state)||!Array.isArray(p.read))throw Error('denied');
  for(const k of ['app_id','issuer_device'])unhex(p[k],16);
  for(const k of ['display_name_hash','oauth_client_id_hash'])unhex(p[k],32);
  if(!Number.isSafeInteger(p.issued_at)||p.issued_at<0)throw Error('denied');
  const seen=new Set();for(const e of p.read){exact(e,['profile','scopes']);unhex(e.profile,16);if(seen.has(e.profile))throw Error('denied');seen.add(e.profile);if(e.scopes!==null){if(!Array.isArray(e.scopes)||!e.scopes.length||new Set(e.scopes).size!==e.scopes.length)throw Error('denied');for(const s of e.scopes)unhex(s,16);}}
  if(p.remember!==null){exact(p.remember,['profile','scope']);unhex(p.remember.profile,16);unhex(p.remember.scope,16);}
  exact(p.limits,['recall_limit_max','requests_per_minute']);
  for(const [k,max]of [['recall_limit_max',20],['requests_per_minute',1000]])if(!Number.isInteger(p.limits[k])||p.limits[k]<1||p.limits[k]>max)throw Error('denied');
  return p;
}
export async function policy(env,app,at=Date.now()) {
  unhex(app,16);const reg=await registry(env);let current;
  const rows=(await records(env.STORAGE,`connectors/${app}/policy/`)).sort((a,b)=>a[1].issued_at-b[1].issued_at||a[0].localeCompare(b[0]));
  for(const [path,p]of rows){try{policyShape(p);if(!new RegExp(`^connectors/${app}/policy/[0-7][0-9A-HJKMNP-TV-Z]{25}\\.json$`).test(path)||p.app_id!==app||p.issued_at>at)continue;await verify(p,await reg.publicKey(p.issuer_device,p.issued_at),'connector-policy');current=p;}catch{}}
  return current;
}
export async function secretWindow(env,app,at=Date.now()) {
  unhex(app,16);const reg=await registry(env),closed=[];
  for(const [path,c]of await records(env.STORAGE,`connectors/${app}/secret-close/`)){try{
    exact(c,['format','signature','app_id','closed_at','issuer_device']);
    if(c.format!=='agentmesh-connector-secret-close-v1'||c.app_id!==app||!Number.isSafeInteger(c.closed_at)||c.closed_at<0||c.closed_at>at||!new RegExp(`^connectors/${app}/secret-close/[0-7][0-9A-HJKMNP-TV-Z]{25}\\.json$`).test(path))continue;
    // A later revocation must not undo a valid reduction; no scope authority is needed.
    await verify(c,await reg.publicKey(c.issuer_device,c.closed_at),'connector-secret-close');
    closed.push(c.closed_at);
  }catch{}}
  for(const [path,w]of await records(env.STORAGE,`connectors/${app}/secret/`)){try{
    exact(w,['format','signature','app_id','not_before','not_after','issuer_device']);
    if(w.format!=='agentmesh-connector-secret-window-v1'||w.app_id!==app||!Number.isSafeInteger(w.not_before)||!Number.isSafeInteger(w.not_after)||w.not_after-w.not_before>3600000||!(w.not_before<=at&&at<w.not_after))continue;
    await verify(w,await reg.publicKey(w.issuer_device,at),'connector-secret-window');
    if(closed.some(t=>w.not_before<=t&&t<w.not_after))continue;return w;
  }catch{}}return null;
}
