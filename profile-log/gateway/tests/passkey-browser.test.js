import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { webauthnScript } from '../src/webauthn.js';
for(const register of [true,false])test('P6-P6 browser '+(register?'registration':'approval')+' converts buffers, clears code and confirms policy',async()=>{
 const listeners={},output={textContent:''},confirm={hidden:true,addEventListener:(name,fn)=>listeners.confirm=fn};
 const form={dataset:{ceremony:register?'register':'approve'},elements:{csrf:{value:'initial'},code:{value:'synthetic-code'}},addEventListener:(name,fn)=>listeners.submit=fn};
 const posts=[],array=new Uint8Array([255,0,1]).buffer;
 const context={document:{querySelector:q=>q==='form[data-ceremony]'?form:q==='#confirm'?confirm:output},location:{pathname:register?'/status/passkeys':'/status/approve'},atob,btoa,Uint8Array,Error,fetch:async(path,options)=>{
  const body=JSON.parse(options.body);posts.push(body);
  if(body.step.endsWith('_begin'))return {ok:true,json:async()=>({options:{challenge:'_wAB',...(register?{user:{id:'_wAB'},excludeCredentials:[{id:'_wAB'}]}:{allowCredentials:[{id:'_wAB'}]})},device_id:'device',challenge_id:'challenge',code_hash:'hash',csrf:'next',client_name:'<script>client</script>',profile_names:['personal','work'],remember_name:'personal/inbox',policy:{max_label:'internal',limits:{recall_limit_max:20,requests_per_minute:60}}})};
  return {ok:true,json:async()=>({status:'ok'})};
 },navigator:{credentials:{[register?'create':'get']:async({publicKey})=>{
  assert.deepEqual([...new Uint8Array(publicKey.challenge)],[255,0,1]);
  assert.deepEqual([...new Uint8Array(register?publicKey.user.id:publicKey.allowCredentials[0].id)],[255,0,1]);
  return {id:'_wAB',rawId:array,type:'public-key',response:{clientDataJSON:array,...(register?{attestationObject:array}:{authenticatorData:array,signature:array,userHandle:null})}};
 }}}};
 vm.runInNewContext(webauthnScript,context);
 await listeners.submit({preventDefault(){}});assert.equal(form.elements.code.value,'');assert.equal(posts.length,1);assert.equal(confirm.hidden,false);
 if(!register)assert.match(output.textContent,/personal, work/);
 await listeners.confirm();assert.equal(posts.length,2);assert.equal(posts[1].csrf,'next');
 const credential=posts[1][register?'credential':'assertion'];assert.equal(credential.response.clientDataJSON,'_wAB');assert.equal(credential.rawId,'_wAB');assert.equal(posts[1].code,undefined);
 assert.match(output.textContent,register?/Passkey enrolled/:/Continue after approval/);
});
