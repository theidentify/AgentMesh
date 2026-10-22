// Handwritten browser code; conversion only, never WebAuthn verification.
export function creationOptions(value,kind) {
 return {...value,authenticatorSelection:{...value.authenticatorSelection,authenticatorAttachment:kind==='security-key'?'cross-platform':'platform'},hints:[kind==='security-key'?'security-key':'client-device']};
}
export function errorMessage(code) {
 const messages={code_invalid:'Code invalid. Run connector passkey add again.',code_expired:'Code expired. Run connector passkey add again.',code_used:'Code already used. Run connector passkey add again.',NotAllowedError:'You cancelled or the prompt timed out.',InvalidStateError:'This authenticator is already enrolled.',wrong_device:'Another device received this request. Reload and retry.',agentmesh_offline:'No trusted device online. Bring your device online, then reload and retry.'};
 return Object.hasOwn(messages,code)?messages[code]:'Request denied. Reload and retry. If the enrolment code is invalid, expired or used, run connector passkey add again.';
}
export function readiness({secure,origin,expectedOrigin,supported,platform}) {
 return {ready:!!(secure&&origin===expectedOrigin&&supported),checks:[
  {state:secure?'good':'bad',text:secure?'Pass — Secure context.':'Fail — Open the HTTPS status page.'},
  {state:origin===expectedOrigin?'good':'bad',text:origin===expectedOrigin?'Pass — Correct status origin.':'Fail — Open the configured status origin.'},
  {state:supported?'good':'bad',text:supported?'Pass — Browser supports passkeys.':'Fail — Use a browser with passkey support.'},
  {state:platform?'good':'warning',text:platform?'Pass — Touch ID, Windows Hello or phone unlock available.':'Warning — Device unlock unavailable. Enable it in device settings, or use a security key or password manager.'}
 ]};
}
export const webauthnScript="'use strict';\n"+[creationOptions, errorMessage, readiness].map(fn=>fn.toString()).join('\n')+String.raw`
const form=document.querySelector('form[data-ceremony]');
const output=document.querySelector('#outcome');
const confirm=document.querySelector('#confirm');
const register=form.dataset.ceremony==='register';
const stage=id=>document.querySelector('#'+id);
let pending=null,ready=false,busy=false;
function show(id){
 for(const name of ['readiness',...(register?['kind']:[]),'code-step','review','result'])stage(name).hidden=name!==id;
 stage(id).focus();
}
function fail(error){output.textContent=errorMessage(error.code||error.name);show('result');}
// Some browsers and extensions never settle these probes; never let readiness hang.
const within=(promise,ms)=>Promise.race([promise,new Promise(resolve=>setTimeout(()=>resolve(undefined),ms))]);
async function checkReadiness(){
 let platform=false;
 const supported=typeof PublicKeyCredential!=='undefined';
 if(supported)try{platform=(await within(PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable(),2000))===true;}catch{}
 const result=readiness({secure:globalThis.isSecureContext,origin:location.origin,expectedOrigin:form.dataset.origin,supported,platform});
 ready=result.ready;
 stage('checks').replaceChildren(...result.checks.map(check=>{const li=document.createElement('li');li.className=check.state;li.textContent=check.text;return li;}));
 stage('ready-next').disabled=!ready;
 if(supported&&PublicKeyCredential.getClientCapabilities)try{
  const caps=(await within(PublicKeyCredential.getClientCapabilities(),2000))||{};
  stage('capabilities').textContent='Browser info only — hybrid: '+(caps.hybrid===true?'yes':'not reported')+'; platform passkeys: '+(caps.passkeyPlatformAuthenticator===true?'yes':'not reported')+'.';
 }catch{}
}
stage('ready-next').addEventListener('click',()=>{if(ready)show(register?'kind':'code-step');});
if(register)stage('kind-next').addEventListener('click',()=>show('code-step'));
checkReadiness();
const decode=s=>Uint8Array.from(atob(s.replace(/-/g,'+').replace(/_/g,'/')+'='.repeat((4-s.length%4)%4)),c=>c.charCodeAt(0)).buffer;
const encode=b=>btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
async function post(body){
 const response=await fetch(location.pathname,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({...body,csrf:form.elements.csrf.value})});
 const result=await response.json();
 if(!response.ok||result.error){const error=Error('denied');error.code=result.error;throw error;}
 if(result.csrf)form.elements.csrf.value=result.csrf;
 return result;
}
function options(value){
 const result={...value,challenge:decode(value.challenge)};
 if(value.user)result.user={...value.user,id:decode(value.user.id)};
 for(const key of ['allowCredentials','excludeCredentials'])if(value[key])result[key]=value[key].map(c=>({...c,id:decode(c.id)}));
 return result;
}
function credential(value){
 const response={clientDataJSON:encode(value.response.clientDataJSON)};
 for(const key of ['attestationObject','authenticatorData','signature','userHandle'])if(value.response[key])response[key]=encode(value.response[key]);
 return {id:value.id,rawId:encode(value.rawId),type:value.type,response};
}
form.addEventListener('submit',async event=>{
 event.preventDefault();if(!ready||busy||pending)return;busy=true;confirm.hidden=true;output.textContent='Contacting trusted device…';
 const code=register?form.elements.code.value.trim().toUpperCase():form.elements.code.value.trim();form.elements.code.value='';
 try{
  pending=await post({step:register?'register_begin':'approve_begin',code});
  output.textContent='';
  stage('summary').textContent=register?'Ready to create your '+(form.elements.kind.value==='security-key'?'security key':'password manager or device')+' passkey.':pending.client_name+' — Read '+pending.profile_names.join(', ')+' at '+pending.policy.max_label+' (and general); remember '+pending.remember_name+'. Recall limit '+pending.policy.limits.recall_limit_max+', requests/minute '+pending.policy.limits.requests_per_minute+'. Cross-profile recall disabled.';
  confirm.textContent=register?'Create passkey':'Approve with passkey';confirm.hidden=false;show('review');
 }catch(error){pending=null;fail(error);}finally{busy=false;}
});
confirm.addEventListener('click',async()=>{
 if(!pending)return;const current=pending;pending=null;confirm.hidden=true;
 try{
  const value=register?await navigator.credentials.create({publicKey:creationOptions(options(current.options),form.elements.kind.value)}):await navigator.credentials.get({publicKey:options(current.options)});
  await post({step:register?'register_finish':'approve_finish',device_id:current.device_id,challenge_id:current.challenge_id,...(register?{credential:credential(value)}:{code_hash:current.code_hash,assertion:credential(value)})});
  output.textContent=register?'Passkey enrolled.':'Approved. Return to the app and click Continue after approval.';
  stage('success-next').hidden=false;show('result');
 }catch(error){fail(error);}
});
`;
