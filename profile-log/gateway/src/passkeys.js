import { statusPage } from './status-ui.js';
import { bytes,hex,sha,unhex,exact } from './encoding.js';
import { tunnel,claim } from './gateway.js';
import { STATUS_APP } from './status-metrics.js';
import { statusResponse } from './status.js';
import { webauthnScript } from './webauthn.js';
const response=(value,status=200)=>statusResponse(JSON.stringify(value),status,'application/json');
async function csrf(env,identity){
 if(typeof identity.sub!=='string'||!identity.sub)throw Error();
 const token=hex(crypto.getRandomValues(new Uint8Array(32)));
 await env.OAUTH_KV.put('status:csrf:'+await sha(bytes(token)),JSON.stringify({...identity,exp:Date.now()+900000}),{expirationTtl:900});return token;
}
async function pending(env,hash){
 unhex(hash,32);const p=await env.OAUTH_KV.get('pending:'+hash,'json');
 if(!p||p.approval||p.completed||p.rejected||p.expires<=Date.now()||typeof p.client_id!=='string'||typeof p.client_name!=='string')throw Error();return p;
}
export async function passkeyPage(request,env,identity,applyAdmin,fetcher){
 const path=new URL(request.url).pathname,register=path==='/status/passkeys';
 if(request.method==='GET'){
  if(path==='/status/webauthn.js')return statusResponse(webauthnScript,200,'text/javascript; charset=utf-8');
  const token=await csrf(env,identity),title=register?'Enrol a passkey':'Approve a connector';
  const html=statusPage(title,'<section class="panel ceremony"><form data-ceremony="'+(register?'register':'approve')+'" method="post"><input type="hidden" name="csrf" value="'+token+'"><label>'+(register?'Device enrolment code':'Six-digit app approval code')+' <input name="code" type="password" autocomplete="off" required maxlength="'+(register?'10':'6')+'"></label><button class="primary">Continue</button></form><p id="outcome" role="status"></p><button class="primary" id="confirm" hidden>Continue with passkey</button><a href="/status">Return to status</a><script src="/status/webauthn.js" defer></script></section>');
  const result=statusResponse(html,200,'text/html; charset=utf-8');result.headers.set('Content-Security-Policy',"default-src 'none'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'");return result;
 }
 try{
  if(request.headers.get('Origin')!==env.STATUS_ORIGIN||![null,'same-origin'].includes(request.headers.get('Sec-Fetch-Site'))||!request.headers.get('Content-Type')?.startsWith('application/json')||Number(request.headers.get('Content-Length')??0)>65536)throw Error();
  const raw=await request.text();if(bytes(raw).length>65536)throw Error();
  const {csrf:token,...args}=JSON.parse(raw);unhex(token,32);
  const hash=await sha(bytes(token)),key='status:csrf:'+hash,stored=await env.OAUTH_KV.get(key,'json');
  if(typeof identity.sub!=='string'||!identity.sub||!stored||stored.sub!==identity.sub||stored.email!==identity.email||!Number.isSafeInteger(stored.exp)||stored.exp<=Date.now())throw Error();
  if(!await claim(env,'status-csrf/'+hash))throw Error();await env.OAUTH_KV.delete(key);
  const schemas={register_begin:['step','code'],register_finish:['step','device_id','challenge_id','credential'],approve_begin:['step','code'],approve_finish:['step','device_id','challenge_id','code_hash','assertion']};
  if(!Object.hasOwn(schemas,args.step)||register!==args.step.startsWith('register_'))throw Error();exact(args,schemas[args.step]);
  let outgoing={...args},p,codeHash;
  if(args.step==='register_begin'){if(typeof args.code!=='string'||!/^[A-Z2-9]{10}$/.test(args.code))throw Error();}
  if(args.step==='approve_begin'){
   if(typeof args.code!=='string'||!/^\d{6}$/.test(args.code))throw Error();codeHash=await sha(bytes(args.code));p=await pending(env,codeHash);
   outgoing={step:args.step,code_hash:codeHash,client_id_hash:await sha(bytes(p.client_id)),client_name:p.client_name};
  }
  if(args.step.endsWith('_finish')){unhex(args.device_id,16);unhex(args.challenge_id,32);}
  if(args.step==='approve_finish'){codeHash=args.code_hash;p=await pending(env,codeHash);}
  const result=await tunnel(env,STATUS_APP,'passkey',outgoing,fetcher);
  if(result.error)return response({error:['agentmesh_offline','wrong_device'].includes(result.error)?result.error:'denied'},result.error==='agentmesh_offline'?503:403);
  if(args.step==='approve_finish'){
   if(result.action!=='approve'||result.payload?.code_hash!==codeHash||result.device_id!==args.device_id)throw Error();
   if(!(await applyAdmin(result,'approve')).ok)throw Error();return response({status:'ok'});
  }
  if(args.step==='register_finish'){exact(result,['status']);if(result.status!=='ok')throw Error();return response({status:'ok'});}
  // Options are ephemeral metadata. Never persist bridge response, credential or assertion.
  const keys=['options','device_id','challenge_id'];if(!register)keys.push('policy','profile_names','remember_name');exact(result,keys);
  unhex(result.device_id,16);unhex(result.challenge_id,32);
  const rp=new URL(env.STATUS_ORIGIN).hostname;
  if((register?result.options?.rp?.id:result.options?.rpId)!==rp)throw Error();
  if(!register){
   if(!Array.isArray(result.profile_names)||!result.profile_names.length||result.profile_names.some(n=>typeof n!=='string'||n.length>256)||typeof result.remember_name!=='string'||result.remember_name.length>256)throw Error();
   const policy=result.policy;exact(policy,['read','remember','max_label','allow_cross_profile','limits']);
   if(!['general','internal'].includes(policy.max_label)||policy.allow_cross_profile!==false)throw Error();
  }
  return response({...result,...(!register?{client_name:p.client_name,code_hash:codeHash}:{}),csrf:await csrf(env,identity)});
 }catch{return response({error:'denied'},403);}
}
