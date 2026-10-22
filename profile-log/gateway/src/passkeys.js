import { statusPage, escape } from './status-ui.js';
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
  const html=statusPage(title,`<section class="panel ceremony">
<noscript><p>JavaScript is required for passkeys. Enable JavaScript for this status site and reload.</p></noscript>
<form data-ceremony="${register?'register':'approve'}" data-origin="${escape(env.STATUS_ORIGIN)}" method="post">
<input type="hidden" name="csrf" value="${token}">
<section id="readiness" tabindex="-1"><h2>1. Check browser readiness</h2><ul id="checks"><li>Checking secure context, origin and passkey support…</li></ul><p id="capabilities" class="muted"></p><button type="button" class="primary" id="ready-next" disabled>Continue</button></section>
${register?`<section id="kind" tabindex="-1" hidden><h2>2. Choose your passkey kind</h2>
<label class="kind-choice"><input type="radio" name="kind" value="platform" checked> Password manager or this device <span>Bitwarden, iCloud Keychain, Google Password Manager, Touch ID / Windows Hello. Password managers can sync across devices; device-only passkeys may not. A password-manager account compromise equals a passkey compromise.</span></label>
<label class="kind-choice"><input type="radio" name="kind" value="security-key"> Security key <span>YubiKey or another physical key: strongest protection; you must carry it.</span></label><p class="muted">This choice is a browser preference. Your trusted device still issues and verifies the passkey.</p><button type="button" id="kind-next" class="primary">Use this kind</button></section>`:''}
<section id="code-step" tabindex="-1" hidden><h2>${register?'3. Enter the device code':'2. Enter the app code'}</h2>
<p id="code-help">${register?'Run <code>connector passkey add</code> on your trusted device. Enter its 10-character code (letters A–Z and digits 2–9). Lowercase is converted to uppercase. Valid 10 minutes, single use.':'Enter the six-digit code shown by the app. You will review the requested access before approving.'}</p>
<label>${register?'Device enrolment code':'Six-digit app approval code'} <input name="code" type="password" autocomplete="off" autocapitalize="characters" spellcheck="false" aria-describedby="code-help" required minlength="${register?'10':'6'}" maxlength="${register?'10':'6'}" pattern="${register?'[A-Za-z2-9]{10}':'[0-9]{6}'}"></label><button class="primary">Check code with device</button></section>
</form>
<section id="review" tabindex="-1" hidden><h2>${register?'4. Create your passkey':'3. Review access'}</h2><p id="summary"></p><button class="primary" id="confirm" hidden>Continue with passkey</button></section>
<p id="outcome" role="status" aria-live="polite"></p>
<section id="result" tabindex="-1" hidden><h2>Result</h2><div id="success-next" hidden>${register?'<p>Next: <a href="/status/approve">approve a connector at /status/approve</a>.</p><p>List with <code>connector passkey list</code>; remove with <code>connector passkey remove HASH_PREFIX</code> on your device.</p>':''}</div><p><a href="${register?'/status/passkeys':'/status/approve'}">Start again</a></p></section>
<a href="/status">Return to status</a><script src="/status/webauthn.js" defer></script></section>`);
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
   if(!(await applyAdmin(result,'approve')).ok)throw Error();await env.OAUTH_KV.delete('status:inventory');return response({status:'ok'});
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
