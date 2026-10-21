/** Bind to the same opaque prefix as device S3 sync, without delete capability. */
export function namespaced(env) {
 const prefix=env.R2_PREFIX??'';
 if(prefix==='')return env;
 if(typeof prefix!=='string'||prefix.includes('..')||!/^(?:[A-Za-z0-9_-][A-Za-z0-9_.-]*\/)+$/.test(prefix))throw Error('invalid_request');
 const bucket=env.STORAGE;
 return {...env,STORAGE:{
  get:key=>bucket.get(prefix+key),
  put:(key,value,options)=>bucket.put(prefix+key,value,options),
  list:async options=>{const page=await bucket.list({...options,prefix:prefix+(options.prefix??'')});return {...page,objects:page.objects.map(o=>({...o,key:o.key.slice(prefix.length)}))};},
 }};
}
