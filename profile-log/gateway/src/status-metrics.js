export const ERROR_CODES=['denied','invalid_request','unavailable','agentmesh_offline','rate_limited','invalid_token'];
export const STATUS_APP='0'.repeat(32);
export const hour=at=>new Date(at).toISOString().slice(0,13).replace(/[-T]/g,'');
export function background(env,operation) {
 const task=Promise.resolve().then(operation).catch(()=>{});try{env.STATUS_WAIT_UNTIL?.(task);}catch{}
}
export function countError(env,code) {
 if(!ERROR_CODES.includes(code))return;
 // Best-effort read-modify-write: concurrent increments can lose counts.
 background(env,async()=>{const key=`status:err:${hour(Date.now())}`,old=await env.OAUTH_KV.get(key,'json'),counts={};for(const c of ERROR_CODES)if(Number.isSafeInteger(old?.[c])&&old[c]>=0)counts[c]=old[c];counts[code]=(counts[code]??0)+1;await env.OAUTH_KV.put(key,JSON.stringify(counts),{expirationTtl:172800});});
}
export function bridgeSeen(env,device,latency) {
 background(env,()=>env.OAUTH_KV.put('status:bridge:'+(device??'unattributed'),JSON.stringify({device_id:device,last_seen_ms:Date.now(),latency_ms:latency}),{expirationTtl:604800}));
}
export function statusShape(value) {
 const integer=n=>Number.isSafeInteger(n)&&n>=0,optional=n=>n===null||integer(n);
 if(!value||!/^[0-9a-f]{32}$/.test(value.device_id)||!integer(value.now_ms)||!['last_control_sync_ms','last_drain_ms','outbox_cursor_ms'].every(k=>optional(value[k]))||!['stored','rejected','errors'].every(k=>integer(value.last_drain_result?.[k])))throw Error('unavailable');
 return {device_id:value.device_id,now_ms:value.now_ms,last_control_sync_ms:value.last_control_sync_ms,last_drain_ms:value.last_drain_ms,last_drain_result:{stored:value.last_drain_result.stored,rejected:value.last_drain_result.rejected,errors:value.last_drain_result.errors},outbox_cursor_ms:value.outbox_cursor_ms};
}

export async function errorCounts(env,at) {
 const errors=Object.fromEntries(ERROR_CODES.map(c=>[c,0]));
 // Remove legacy reads 48 hours after the last old-format writer is retired.
 await Promise.all(Array.from({length:24},async(_,i)=>{
  const stamp=hour(at-i*3600000),[counts,...legacy]=await Promise.all([env.OAUTH_KV.get(`status:err:${stamp}`,'json'),...ERROR_CODES.map(c=>env.OAUTH_KV.get(`status:err:${c}:${stamp}`))]);
  for(const [index,code]of ERROR_CODES.entries())for(const n of [counts?.[code],Number(legacy[index]??0)])if(Number.isSafeInteger(n)&&n>=0)errors[code]+=n;
 }));return errors;
}
