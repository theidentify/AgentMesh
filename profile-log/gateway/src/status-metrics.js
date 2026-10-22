export const ERROR_CODES=['denied','invalid_request','unavailable','agentmesh_offline','rate_limited','invalid_token'];
export const STATUS_APP='0'.repeat(32);
export const hour=at=>new Date(at).toISOString().slice(0,13).replace(/[-T]/g,'');
export function background(env,operation) {
 const task=Promise.resolve().then(operation).catch(()=>{});try{env.STATUS_WAIT_UNTIL?.(task);}catch{}
}
export function countError(env,code) {
 if(!ERROR_CODES.includes(code))return;
 background(env,async()=>{const key=`status:err:${code}:${hour(Date.now())}`,old=Number(await env.OAUTH_KV.get(key)??0);await env.OAUTH_KV.put(key,String((Number.isSafeInteger(old)&&old>=0?old:0)+1),{expirationTtl:172800});});
}
export function bridgeSeen(env,device,latency) {
 background(env,()=>env.OAUTH_KV.put('status:bridge:'+(device??'unattributed'),JSON.stringify({device_id:device,last_seen_ms:Date.now(),latency_ms:latency}),{expirationTtl:604800}));
}
export function statusShape(value) {
 const integer=n=>Number.isSafeInteger(n)&&n>=0,optional=n=>n===null||integer(n);
 if(!value||!/^[0-9a-f]{32}$/.test(value.device_id)||!integer(value.now_ms)||!['last_control_sync_ms','last_drain_ms','outbox_cursor_ms'].every(k=>optional(value[k]))||!['stored','rejected','errors'].every(k=>integer(value.last_drain_result?.[k])))throw Error('unavailable');
 return {device_id:value.device_id,now_ms:value.now_ms,last_control_sync_ms:value.last_control_sync_ms,last_drain_ms:value.last_drain_ms,last_drain_result:{stored:value.last_drain_result.stored,rejected:value.last_drain_result.rejected,errors:value.last_drain_result.errors},outbox_cursor_ms:value.outbox_cursor_ms};
}
