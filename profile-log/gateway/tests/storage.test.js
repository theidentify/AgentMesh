import test from 'node:test';
import assert from 'node:assert/strict';
import { namespaced } from '../src/storage.js';
import { FakeR2 } from './fakes.mjs';
test('P6-1/P6-6 R2 prefix confines all accessor reads and writes',async()=>{
 const bucket=new FakeR2(),env=namespaced({STORAGE:bucket,R2_PREFIX:'v1/'});
 for(const key of ['registry/devices/a','registry/revocations/a','registry/gateway/a','registry/outbox-keys/a','connectors/a/policy/a','connectors/a/secret/a','keyring/a','outbox/a']){
  await env.STORAGE.put(key,new TextEncoder().encode('{}'));
  assert.ok(await env.STORAGE.get(key));assert.ok(bucket.data.has('v1/'+key));assert.ok(!bucket.data.has(key));
 }
 let cursor;const keys=[];do{const page=await env.STORAGE.list({prefix:'registry/',cursor});keys.push(...page.objects.map(o=>o.key));cursor=page.truncated?page.cursor:undefined;}while(cursor);
 assert.equal(keys.length,4);assert.ok(keys.every(k=>k.startsWith('registry/')));
 for(const prefix of ['/v1/','v1','../','a/../','a..b/','a//','a\\b/',42])assert.throws(()=>namespaced({STORAGE:bucket,R2_PREFIX:prefix}));
 assert.equal(namespaced({STORAGE:bucket,R2_PREFIX:''}).STORAGE,bucket);
});
