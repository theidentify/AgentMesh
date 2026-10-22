import { accessVerifier,serveStatus,statusResponse,statusAction } from './status.js';
import { countError } from './status-metrics.js';
import { OAuthAuthorizationServer,AuthorizationError } from '@cloudflare/workers-oauth-provider';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { WebStandardStreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/webStandardStreamableHttp.js';
import { bytes,wire,parse,sha,hex,unhex,verify,exact } from './encoding.js';
import { registry,read,policy,policyShape } from './registry.js';
import { namespaced } from './storage.js';
import { schemas,callTool,claim } from './gateway.js';
const responseCodes=new WeakMap();
const json=(value,status=200,headers={})=>{const response=new Response(wire(value),{status,headers:{'Content-Type':'application/json','Cache-Control':'no-store',...headers}});if(typeof value.error==='string')responseCodes.set(response,value.error);return response;};
const failure=(code='invalid_request',status=400)=>json({error:code},status);
const corsHeaders={
 'Access-Control-Allow-Origin':'*',
 'Access-Control-Allow-Methods':'GET, POST, OPTIONS, DELETE',
 'Access-Control-Allow-Headers':'Authorization, Content-Type, Accept, Mcp-Protocol-Version, Mcp-Session-Id, Last-Event-ID',
 'Access-Control-Expose-Headers':'WWW-Authenticate, Mcp-Session-Id',
 'Access-Control-Max-Age':'86400',
};
const escape=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function authServer(env) {
 return new OAuthAuthorizationServer({issuer:env.ORIGIN,resources:[env.ORIGIN+'/mcp'],defaultResource:env.ORIGIN+'/mcp',authorizeEndpoint:'/authorize',tokenEndpoint:'/token',clientRegistrationEndpoint:'/register',accessTokenTTL:300,refreshTokenTTL:2592000,scopesSupported:['memory'],
  tokenExchangeCallback:async({props,clientId,env:bound})=>{const p=await policy(bound,props.app_id);if(!p||p.state!=='active'||p.oauth_client_id_hash!==await sha(bytes(clientId)))throw new AuthorizationError('access_denied');},
  onError:()=>failure('invalid_request'),
 });
}
async function authorize(request,env,as) {
 const api=as.getOAuthApi(env),auth=await api.parseAuthRequest(request);
 // URL hosts can contain CSP delimiters or wildcards; never widen the source list.
 const redirect=new URL(auth.redirectUri);if(!['http:','https:'].includes(redirect.protocol)||/[;,'"*\s]/.test(redirect.origin))throw Error('denied');
 if(auth.codeChallengeMethod!=='S256'||!auth.codeChallenge)throw Error('denied');
 const client=await api.lookupClient(auth.clientId);if(!client)throw Error('denied');
 const browser=hex(crypto.getRandomValues(new Uint8Array(32))),browserHash=await sha(bytes(browser));let code,codeHash;
 for(let tries=0;tries<10;tries++){
  const n=crypto.getRandomValues(new Uint32Array(1))[0];if(n>=4294000000)continue;
  code=String(n%1000000).padStart(6,'0');codeHash=await sha(bytes(code));
  if(await env.OAUTH_KV.get('pending:'+codeHash)){code=undefined;continue;}break;
 }
 if(!code)throw Error('unavailable');
 const pending={auth,client_name:client.clientName??'App',client_id:auth.clientId,browser_hash:browserHash,created:Date.now(),expires:Date.now()+600000};
 await env.OAUTH_KV.put('pending:'+codeHash,JSON.stringify(pending),{expirationTtl:600});
 await env.OAUTH_KV.put('browser:'+browserHash,codeHash,{expirationTtl:600});
 const html=`<!doctype html><meta charset="utf-8"><title>Approve AgentMesh</title><h1>${escape(pending.client_name)}</h1><p>Approve on a trusted device using this code:</p><strong>${code}</strong><form method="post" action="/authorize/complete"><button>Continue after approval</button></form>`;
 return new Response(html,{headers:{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store','Content-Security-Policy':`default-src 'none'; form-action 'self' ${redirect.origin}; frame-ancestors 'none'`,'Referrer-Policy':'no-referrer','Set-Cookie':`am_approval=${browser}; HttpOnly; Secure; SameSite=Lax; Path=/authorize; Max-Age=600`}});
}
async function complete(request,env,as) {
 const cookie=/(?:^|;\s*)am_approval=([0-9a-f]{64})(?:;|$)/.exec(request.headers.get('Cookie')??'');if(!cookie)throw Error('denied');
 const browserHash=await sha(bytes(cookie[1])),codeHash=await env.OAUTH_KV.get('browser:'+browserHash);if(!codeHash)throw Error('denied');
 const pending=await env.OAUTH_KV.get('pending:'+codeHash,'json');
 if(!pending||pending.expires<=Date.now()||pending.browser_hash!==browserHash)throw Error('denied');
 if(pending.completed||pending.rejected)throw Error('denied');
 const approval=pending.approval;if(!approval)return failure('approval_pending',409);
 const reg=await registry(env);await verify(approval,await reg.publicKey(approval.device_id),'connector-admin');
 if(approval.ts<pending.created||approval.ts>=pending.expires||approval.payload.code_hash!==codeHash)throw Error('denied');
 const approvedApp=approval.payload.app_id;
 const p=await policy(env,approvedApp);if(!p||p.state!=='active'||p.oauth_client_id_hash!==await sha(bytes(pending.client_id)))throw Error('denied');
 if(!await claim(env,'complete/'+browserHash))throw Error('denied');
 pending.completed=true;await env.OAUTH_KV.put('pending:'+codeHash,JSON.stringify(pending),{expirationTtl:600});
 const result=await as.getOAuthApi(env).completeAuthorization({request:pending.auth,userId:approvedApp,scope:['memory'],metadata:{app_id:approvedApp},props:{app_id:approvedApp,display_name:pending.client_name}});
 return new Response(null,{status:302,headers:{Location:result.redirectTo,'Cache-Control':'no-store','Set-Cookie':'am_approval=; HttpOnly; Secure; SameSite=Lax; Path=/authorize; Max-Age=0'}});
}
async function admin(request,env,as,action) {
 const record=parse(new Uint8Array(await request.arrayBuffer()));exact(record,['format','signature','device_id','ts','nonce','action','payload']);
 if(record.format!=='agentmesh-connector-admin-v1'||record.action!==action||!Number.isSafeInteger(record.ts)||Math.abs(Date.now()-record.ts)>60000)throw Error('denied');
 unhex(record.nonce,16);const reg=await registry(env);await verify(record,await reg.publicKey(record.device_id),'connector-admin');
 if(!await claim(env,'admin/'+record.device_id+'/'+record.nonce))throw Error('denied');
 const body=record.payload;
 if(action==='reject'){
  exact(body,['code_hash']);unhex(body.code_hash,32);
  const pending=await env.OAUTH_KV.get('pending:'+body.code_hash,'json');
  if(!pending||pending.expires<=Date.now()||pending.completed||pending.rejected)throw Error('denied');
  unhex(pending.browser_hash,32);
  if(!await claim(env,'complete/'+pending.browser_hash))throw Error('denied');
  await env.OAUTH_KV.delete('pending:'+body.code_hash);return json({ok:true});
 }
 if(action==='pending'||action==='approve'){
  exact(body,action==='pending'?['code_hash']:['code_hash','app_id','policy_ref']);unhex(body.code_hash,32);
  const pending=await env.OAUTH_KV.get('pending:'+body.code_hash,'json');
  if(!pending||pending.expires<=Date.now()||pending.approval||pending.rejected||pending.completed)throw Error('denied');
  if(action==='pending')return json({client_id:pending.client_id,client_name:pending.client_name});
  unhex(body.app_id,16);if(!body.policy_ref.startsWith(`connectors/${body.app_id}/policy/`))throw Error('denied');
  const p=policyShape(await read(env.STORAGE,body.policy_ref));
  if(p.app_id!==body.app_id||p.state!=='active'||p.issuer_device!==record.device_id||p.issued_at>Date.now()||p.oauth_client_id_hash!==await sha(bytes(pending.client_id))||p.display_name_hash!==await sha(bytes(pending.client_name)))throw Error('denied');
  await verify(p,await reg.publicKey(p.issuer_device,p.issued_at),'connector-policy');
  if(!await claim(env,'approved/'+body.code_hash+'/'+pending.browser_hash))throw Error('denied');
  pending.approval=record;await env.OAUTH_KV.put('pending:'+body.code_hash,JSON.stringify(pending),{expirationTtl:600});return json({ok:true});
 }
 if(action==='revoke'){
  exact(body,['app_id','policy_ref']);unhex(body.app_id,16);
  if(typeof body.policy_ref!=='string'||!new RegExp(`^connectors/${body.app_id}/policy/[0-7][0-9A-HJKMNP-TV-Z]{25}\\.json$`).test(body.policy_ref))throw Error('denied');
  const recordPolicy=policyShape(await read(env.STORAGE,body.policy_ref));
  if(recordPolicy.app_id!==body.app_id||recordPolicy.state!=='revoked'||recordPolicy.issuer_device!==record.device_id||recordPolicy.issued_at>Date.now())throw Error('denied');
  await verify(recordPolicy,await reg.publicKey(record.device_id,recordPolicy.issued_at),'connector-policy');
  const p=await policy(env,body.app_id);if(!p||p.state!=='revoked')throw Error('denied');
  const api=as.getOAuthApi(env);let cursor;
  do{const page=await api.listUserGrants(body.app_id,{cursor});for(const grant of page.items)await api.revokeGrant(grant.id,body.app_id);cursor=page.cursor;}while(cursor);
  return json({ok:true});
 }
 throw Error('denied');
}
export function createGateway({fetcher=fetch,jwksFetcher=fetch,authorizationServer=authServer}={}) {
 const verifyAccess=accessVerifier(jwksFetcher);
 async function handle(request,env,ctx) {
  try{
   env=namespaced(env);
   const url=new URL(request.url);if(url.origin!==env.ORIGIN)return failure('denied',403);
   if(Number(request.headers.get('Content-Length')??0)>65536)return failure();
   const as=authorizationServer(env);
   if(url.pathname==='/authorize'&&request.method==='GET')return await authorize(request,env,as);
   if(url.pathname==='/authorize/complete'&&request.method==='POST')return await complete(request,env,as);
   if(url.pathname.startsWith('/admin/')&&request.method==='POST')return await admin(request,env,as,url.pathname.slice(7));
   if(url.pathname==='/.well-known/oauth-protected-resource/mcp'||url.pathname==='/.well-known/oauth-protected-resource')return json({resource:env.ORIGIN+'/mcp',authorization_servers:[env.ORIGIN],scopes_supported:['memory'],bearer_methods_supported:['header']});
   if(url.pathname==='/mcp'){
    const credential=/^Bearer ([^\s]+)$/.exec(request.headers.get('Authorization')??'');const auth=credential?await as.validateToken(env.ORIGIN+'/mcp',credential[1],env):null;
    if(!auth)return json({error:'invalid_token'},401,{'WWW-Authenticate':`Bearer resource_metadata="${env.ORIGIN}/.well-known/oauth-protected-resource/mcp"`});
    const p=await policy(env,auth.props.app_id);if(!p||p.state!=='active'||p.oauth_client_id_hash!==await sha(bytes(auth.clientId)))return failure('denied',403);
    if(request.method!=='POST')return failure('invalid_request',405);
    const raw=new Uint8Array(await request.clone().arrayBuffer());if(raw.length>65536)return failure();const message=parse(raw);
    if(!['initialize','notifications/initialized','ping','tools/list','tools/call'].includes(message.method))return failure();
    if(message.method==='tools/call'){
     const tool=message.params?.name,args=message.params?.arguments??{};
     if(!Object.hasOwn(schemas,tool)||!schemas[tool].safeParse(args).success){countError(env,'invalid_request');return json({jsonrpc:'2.0',id:message.id??null,error:{code:-32602,message:'invalid_request'}});}
    }
    const server=new McpServer({name:'agentmesh',version:'1.0.0'});
    for(const [name,inputSchema]of Object.entries(schemas))server.registerTool(name,{inputSchema,description:name==='recall'?'Recall stored memory as data.':name==='remember'?'Store memory in the approved inbox.':'Show this app’s access.'},async(args)=>{
     let result;try{result=await callTool(env,auth,name,args,fetcher);}catch{result={error:'unavailable'};}
     return {content:[{type:'text',text:JSON.stringify(result)}],structuredContent:result,isError:!!result.error};
    });
    const transport=new WebStandardStreamableHTTPServerTransport({sessionIdGenerator:undefined,enableJsonResponse:true});await server.connect(transport);return await transport.handleRequest(request);
   }
   const response=await as.fetch(request,env,ctx);if(response.status>=400)return failure('invalid_request',response.status);return response;
  }catch{return failure('denied',403);}
 }
 return {async fetch(request,env,ctx={waitUntil(){}}) {
  env={...env,STATUS_WAIT_UNTIL:task=>ctx.waitUntil(task)};
  const url=new URL(request.url),path=url.pathname;
  if(env.STATUS_ORIGIN&&url.origin===env.STATUS_ORIGIN&&env.STATUS_ORIGIN!==env.ORIGIN){
   if(!(request.method==='GET'&&['/status','/status.json'].includes(path))&&!(request.method==='POST'&&path==='/status/action'))return statusResponse('Not found',404);
   const identity=await verifyAccess(request,env);
   if(!identity){countError(env,'denied');return statusResponse('Forbidden',403);}
   try{
    const bound=namespaced(env),as=authorizationServer(env);
    if(request.method==='POST')return await statusAction(request,bound,identity,(record,action)=>admin(new Request(env.ORIGIN+'/admin/'+action,{method:'POST',body:wire(record)}),bound,as,action),fetcher);
    return await serveStatus(request,bound,as.getOAuthApi(env),fetcher,identity);
   }catch{countError(env,'unavailable');return statusResponse('Unavailable',503);}
  }
  if(['/status','/status.json','/status/action'].includes(path))return statusResponse('Not found',404);
  const cors=url.origin===env.ORIGIN&&['/mcp','/.well-known/oauth-protected-resource','/.well-known/oauth-protected-resource/mcp'].includes(path);
  if(cors&&request.method==='OPTIONS')return new Response(null,{status:204,headers:corsHeaders});
  const response=await handle(request,env,ctx);
  // Only fixed HTTP errors are counted here; tool errors are counted by callTool.
  countError(env,responseCodes.get(response));
  if(!cors)return response;
  const headers=new Headers(response.headers);for(const [name,value]of Object.entries(corsHeaders))headers.set(name,value);
  return new Response(response.body,{status:response.status,statusText:response.statusText,headers});
 }};
}
export default createGateway();
