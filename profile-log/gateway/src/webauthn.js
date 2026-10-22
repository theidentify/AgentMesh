// Handwritten browser code; conversion only, never WebAuthn verification.
export const webauthnScript=String.raw`
'use strict';
const form=document.querySelector('form[data-ceremony]');
const output=document.querySelector('#outcome');
const confirm=document.querySelector('#confirm');
let pending=null;
const decode=s=>Uint8Array.from(atob(s.replace(/-/g,'+').replace(/_/g,'/')+'='.repeat((4-s.length%4)%4)),c=>c.charCodeAt(0)).buffer;
const encode=b=>btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
async function post(body){
 const response=await fetch(location.pathname,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({...body,csrf:form.elements.csrf.value})});
 const result=await response.json();
 if(!response.ok){output.textContent=result.error==='agentmesh_offline'?'No trusted device is online (agentmesh_offline).':result.error==='wrong_device'?'Another device received this request (wrong_device). Reload and retry.':'Request denied. Reload and retry.';throw Error('denied');}
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
 event.preventDefault();confirm.hidden=true;pending=null;output.textContent='Contacting trusted device…';
 const code=form.elements.code.value;form.elements.code.value='';
 try{
  const register=form.dataset.ceremony==='register';
  pending=await post({step:register?'register_begin':'approve_begin',code});
  output.textContent=register?'Ready to enrol a passkey.':pending.client_name+' — Read '+pending.profile_names.join(', ')+' at '+pending.policy.max_label+' (and general); remember '+pending.remember_name+'. Recall limit '+pending.policy.limits.recall_limit_max+', requests/minute '+pending.policy.limits.requests_per_minute+'. Cross-profile recall disabled.';
  confirm.textContent=register?'Create passkey':'Approve with passkey';confirm.hidden=false;
 }catch{pending=null;}
});
confirm.addEventListener('click',async()=>{
 if(!pending)return;const current=pending;pending=null;confirm.hidden=true;
 try{
  const register=form.dataset.ceremony==='register';
  const value=register?await navigator.credentials.create({publicKey:options(current.options)}):await navigator.credentials.get({publicKey:options(current.options)});
  await post({step:register?'register_finish':'approve_finish',device_id:current.device_id,challenge_id:current.challenge_id,...(register?{credential:credential(value)}:{code_hash:current.code_hash,assertion:credential(value)})});
  output.textContent=register?'Passkey enrolled.':'Approved. Return to the app and click Continue after approval.';
 }catch{if(!output.textContent.includes('retry')&&!output.textContent.includes('agentmesh_offline'))output.textContent='Passkey operation did not complete. Reload and retry.';}
});
`;
