import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { webauthnScript } from '../src/webauthn.js';
for(const [register,kind,failure] of [[true,'platform'],[true,'security-key'],[false,'platform'],[true,'platform','NotAllowedError'],[false,'platform','agentmesh_offline'],[true,'platform','unsupported'],[false,'platform','wrong-origin']])test('P6-P6 browser '+(register?'registration':'approval')+' '+kind+' '+(failure||'success')+' converts buffers, clears code and confirms policy',async()=>{
 const listeners={},output={textContent:''},confirm={hidden:true,addEventListener:(name,fn)=>listeners.confirm=fn};
 const nodes={};const node=q=>q==='#kind'&&!register?null:nodes[q]??={hidden:true,textContent:'',focus(){},replaceChildren(...children){this.children=children;},addEventListener(name,fn){this[name]=fn;}};
 const form={dataset:{ceremony:register?'register':'approve',origin:'https://status.example'},elements:{kind:{value:kind},csrf:{value:'initial'},code:{value:register?'abcdefghjk':'123456'}},addEventListener:(name,fn)=>listeners.submit=fn};
 const posts=[],array=new Uint8Array([255,0,1]).buffer;
 const context={setTimeout,clearTimeout,isSecureContext:true,PublicKeyCredential:failure==='unsupported'?undefined:{isUserVerifyingPlatformAuthenticatorAvailable:async()=>false,getClientCapabilities:async()=>({hybrid:true,passkeyPlatformAuthenticator:false})},document:{createElement:()=>({}),querySelector:q=>q==='form[data-ceremony]'?form:q==='#confirm'?confirm:q==='#outcome'?output:node(q)},location:{origin:failure==='wrong-origin'?'https://wrong.example':'https://status.example',pathname:register?'/status/passkeys':'/status/approve'},atob,btoa,Uint8Array,Error,fetch:async(path,options)=>{
  const body=JSON.parse(options.body);posts.push(body);
  if(failure==='agentmesh_offline')return {ok:false,json:async()=>({error:failure})};
  if(body.step.endsWith('_begin'))return {ok:true,json:async()=>({options:{challenge:'_wAB',...(register?{user:{id:'_wAB'},excludeCredentials:[{id:'_wAB'}]}:{allowCredentials:[{id:'_wAB'}]})},device_id:'device',challenge_id:'challenge',code_hash:'hash',csrf:'next',client_name:'<script>client</script>',profile_names:['personal','work'],remember_name:'personal/inbox',policy:{max_label:'internal',limits:{recall_limit_max:20,requests_per_minute:60}}})};
  return {ok:true,json:async()=>({status:'ok'})};
 },navigator:{credentials:{[register?'create':'get']:async({publicKey})=>{
  if(failure==='NotAllowedError'){const error=Error();error.name=failure;throw error;}
  if(register)assert.equal(publicKey.authenticatorSelection.authenticatorAttachment,kind==='security-key'?'cross-platform':'platform');
  else assert.equal(publicKey.hints,undefined);
  assert.deepEqual([...new Uint8Array(publicKey.challenge)],[255,0,1]);
  assert.deepEqual([...new Uint8Array(register?publicKey.user.id:publicKey.allowCredentials[0].id)],[255,0,1]);
  return {id:'_wAB',rawId:array,type:'public-key',response:{clientDataJSON:array,...(register?{attestationObject:array}:{authenticatorData:array,signature:array,userHandle:null})}};
 }}}};
 vm.runInNewContext(webauthnScript,context);
 await new Promise(resolve=>setImmediate(resolve));
 if(['unsupported','wrong-origin'].includes(failure)){assert.equal(node('#ready-next').disabled,true);await listeners.submit({preventDefault(){}});assert.equal(posts.length,0);return;}
 assert.match(node('#capabilities').textContent,/info only.*hybrid: yes/);
 assert.equal(node('#ready-next').disabled,false);
 node('#ready-next').click();assert.equal(node(register?'#kind':'#code-step').hidden,false);
 if(register){node('#kind-next').click();assert.equal(node('#code-step').hidden,false);}
 await listeners.submit({preventDefault(){}});assert.equal(form.elements.code.value,'');assert.equal(posts.length,1);assert.equal(posts[0].code,register?'ABCDEFGHJK':'123456');
 if(failure==='agentmesh_offline'){assert.match(output.textContent,/No trusted device/);assert.equal(node('#result').hidden,false);return;}
 assert.equal(confirm.hidden,false);assert.equal(node('#review').hidden,false);
 if(!register)assert.match(node('#summary').textContent,/personal, work/);
 await listeners.confirm();
 if(failure==='NotAllowedError'){assert.match(output.textContent,/cancelled.*timed out/);assert.equal(posts.length,1);assert.equal(node('#result').hidden,false);return;}
 assert.equal(node('#result').hidden,false);assert.equal(posts.length,2);assert.equal(posts[1].csrf,'next');
 const credential=posts[1][register?'credential':'assertion'];assert.equal(credential.response.clientDataJSON,'_wAB');assert.equal(credential.rawId,'_wAB');assert.equal(posts[1].code,undefined);
 assert.match(output.textContent,register?/Passkey enrolled/:/Continue after approval/);
});

test('creation kind changes only browser preferences',async()=>{
 const {creationOptions}=await import('../src/webauthn.js');
 const source={challenge:'challenge',rp:{id:'status.example'},user:{id:'user'},authenticatorSelection:{residentKey:'required',userVerification:'required'},excludeCredentials:[]};
 const before=structuredClone(source);
 for(const [kind,attachment,hint] of [['platform','platform','client-device'],['security-key','cross-platform','security-key']]){
  const result=creationOptions(source,kind);
  assert.equal(result.authenticatorSelection.authenticatorAttachment,attachment);
  assert.deepEqual(result.hints,[hint]);
  for(const key of ['challenge','rp','user','excludeCredentials'])assert.equal(result[key],source[key]);
  assert.equal(result.authenticatorSelection.residentKey,'required');assert.equal(result.authenticatorSelection.userVerification,'required');
 }
 assert.deepEqual(source,before);
});
test('readiness requires secure expected origin and support, not platform availability',async()=>{
 const {readiness}=await import('../src/webauthn.js');
 const base={secure:true,origin:'https://status.example',expectedOrigin:'https://status.example',supported:true,platform:false};
 assert.equal(readiness(base).ready,true);
 assert.equal(readiness(base).checks[3].state,'warning');
 for(const change of [{secure:false},{origin:'https://other.example'},{supported:false}])assert.equal(readiness({...base,...change}).ready,false);
});
test('friendly errors use fixed messages without reflecting device text',async()=>{
 const {errorMessage}=await import('../src/webauthn.js');
 for(const code of ['code_invalid','code_expired','code_used'])assert.match(errorMessage(code),/connector passkey add again/);
 assert.match(errorMessage('NotAllowedError'),/cancelled.*timed out/);
 assert.match(errorMessage('InvalidStateError'),/already enrolled/);
 assert.match(errorMessage('wrong_device'),/retry/);
 assert.match(errorMessage('agentmesh_offline'),/No trusted device/);
 assert.match(errorMessage('<script>'),/^Request denied/);
});
