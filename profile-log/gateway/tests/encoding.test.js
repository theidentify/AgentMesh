import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { typed,hex,verify,parse,bytes } from '../src/encoding.js';
import { vectorSeal } from './vector-seal.mjs';
const v=JSON.parse(await readFile(new URL('../../tests/vectors/phase6/v1.json',import.meta.url),'utf8'));
test('Phase 6 Python typed/signature/HPKE vectors',async()=>{
  assert.equal(hex(typed(v.typed_value)),v.typed_hex);
  await verify(v.record,v.public,'gateway'); await verify(v.request,v.public,'gateway-request');
  assert.equal(await vectorSeal(v.outbox.public,v.outbox.info,hex(bytes(JSON.stringify(v.outbox.payload,Object.keys(v.outbox.payload).sort())))),v.outbox.ciphertext);
  await assert.rejects(verify({...v.request,app_id:'ff'.repeat(16)},v.public,'gateway-request'));
});
test('strict storage JSON refuses duplicate keys and invalid scalars',()=>{
  for(const s of ['{"a":1,"a":2}','{"a":1e999}','"\\ud800"','[1,]'])assert.throws(()=>parse(bytes(s)));
});
test('FORMAT typed distinguishes JSON floats and full signed int64 integers',()=>{
 assert.equal(hex(typed(parse(bytes('1.0')))),'643ff0000000000000');
 assert.equal(hex(typed(parse(bytes('-0.0')))),'648000000000000000');
 assert.equal(new TextDecoder().decode(typed(parse(bytes('9223372036854775807')))),'i9223372036854775807;');
 assert.throws(()=>parse(bytes('9223372036854775808')));
 assert.throws(()=>typed(Array(100001).fill(null)));
});
