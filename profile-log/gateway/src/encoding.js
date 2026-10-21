import { Buffer } from 'node:buffer';
/** FORMAT typed encoding; no cryptographic primitives implemented here. */
const encoder = new TextEncoder();
export const bytes = value => encoder.encode(value);
export const hex = value => Buffer.from(value).toString('hex');
export function unhex(value, size) {
  if (typeof value !== 'string' || value.length !== size * 2 || !/^[0-9a-f]+$/.test(value)) throw Error('invalid_request');
  return Uint8Array.from(value.match(/../g), x => parseInt(x, 16));
}
export const concat = (...parts) => {
  const out = new Uint8Array(parts.reduce((n,p) => n + p.length, 0)); let offset = 0;
  for (const p of parts) { out.set(p,offset); offset += p.length; } return out;
};
function scalar(s) { if (typeof s !== 'string' || !s.isWellFormed()) throw Error('invalid_request'); return bytes(s); }
export class Float64 extends Number {}
export function typed(value) {
  const result=encodeTyped(value,0,{count:0});
  if(result.length>1048576)throw Error('invalid_request');return result;
}
function encodeTyped(value, depth, budget) {
  if (++budget.count>100000 || depth > 64) throw Error('invalid_request');
  if (value === null) return bytes('n');
  if (value === true || value === false) return bytes(value ? 't' : 'f');
  if (typeof value === 'bigint') {
    if (value < -(1n<<63n) || value >= (1n<<63n)) throw Error('invalid_request');
    return bytes(`i${value};`);
  }
  if (value instanceof Float64) {const d=new Uint8Array(8),n=Number(value);if(!Number.isFinite(n))throw Error('invalid_request');new DataView(d.buffer).setFloat64(0,n);return concat(bytes('d'),d);}
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw Error('invalid_request');
    if (Number.isInteger(value) && !Object.is(value,-0)) {
      if (!Number.isSafeInteger(value)) throw Error('invalid_request');
      return bytes(`i${value};`);
    }
    const d=new Uint8Array(8); new DataView(d.buffer).setFloat64(0,value); return concat(bytes('d'),d);
  }
  if (typeof value === 'string') { const s=scalar(value); return concat(bytes(`s${s.length}:`),s); }
  if (Array.isArray(value)) return concat(bytes(`l${value.length}:`),...value.map(v=>encodeTyped(v,depth+1,budget)));
  if (typeof value === 'object' && Object.getPrototypeOf(value)===Object.prototype) {
    const keys=Object.keys(value).sort((a,b)=>Buffer.compare(scalar(a),scalar(b)));
    return concat(bytes(`o${keys.length}:`),...keys.flatMap(k=>[encodeTyped(k,depth+1,budget),encodeTyped(value[k],depth+1,budget)]));
  }
  throw Error('invalid_request');
}
export function wire(value) {
 typed(value);
 function json(v){
  if(typeof v==='bigint')return String(v);
  if(v instanceof Float64){const n=Number(v);return Object.is(n,-0)?'-0.0':Number.isInteger(n)?String(n)+'.0':JSON.stringify(n);}
  if(Array.isArray(v))return '['+v.map(json).join(',')+']';
  if(v!==null&&typeof v==='object')return '{'+Object.keys(v).sort((a,b)=>Buffer.compare(bytes(a),bytes(b))).map(k=>JSON.stringify(k)+':'+json(v[k])).join(',')+'}';
  return Object.is(v,-0)?'-0.0':JSON.stringify(v);
 }
 const out=bytes(json(value));if(out.length>1048576)throw Error('invalid_request');return out;
}
export function parse(raw) {
  if (raw.byteLength>1048576) throw Error('invalid_request');
  const s=new TextDecoder('utf-8',{fatal:true}).decode(raw); let i=0, count=0;
  const ws=()=>{ while (/\s/.test(s[i]||'X')) { if (!' \t\r\n'.includes(s[i])) throw Error('invalid_request'); i++; } };
  const string=()=>{ const start=i++; while(i<s.length) { if(s[i]==='\\') { i+=2; continue; } if(s[i++]==='"') { const v=JSON.parse(s.slice(start,i)); scalar(v); return v; } } throw Error('invalid_request'); };
  const value=(depth=0)=>{
    if (++count>100000 || depth>64) throw Error('invalid_request'); ws();
    if(s[i]==='"') return string();
    if(s[i]==='{') { i++; const o={}; const seen=new Set(); ws(); if(s[i]==='}') {i++;return o;}
      while(true) { ws(); if(s[i]!=='"') throw Error('invalid_request'); const k=string(); if(seen.has(k)) throw Error('invalid_request'); seen.add(k); ws(); if(s[i++]!==':') throw Error('invalid_request'); Object.defineProperty(o,k,{value:value(depth+1),enumerable:true,writable:true,configurable:true}); ws(); const end=s[i++]; if(end==='}')return o; if(end!==',')throw Error('invalid_request'); }
    }
    if(s[i]==='[') { i++; const a=[]; ws(); if(s[i]===']'){i++;return a;} while(true){a.push(value(depth+1));ws(); const end=s[i++];if(end===']')return a;if(end!==',')throw Error('invalid_request');} }
    for(const [word,v] of [['null',null],['true',true],['false',false]]) if(s.startsWith(word,i)){i+=word.length;return v;}
    const m=/^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/.exec(s.slice(i));
    if(!m)throw Error('invalid_request');i+=m[0].length;const n=Number(m[0]);if(!Number.isFinite(n))throw Error('invalid_request');if(/[.eE]/.test(m[0]))return new Float64(n);if(!Number.isSafeInteger(n)){const b=BigInt(m[0]);if(b<-(1n<<63n)||b>=(1n<<63n))throw Error('invalid_request');return b;}return n;
  };
  const out=value();ws();if(i!==s.length)throw Error('invalid_request');return out;
}
export const unsigned = record => Object.fromEntries(Object.entries(record).filter(([k])=>k!=='signature'));
export const domain = kind => bytes(`AgentMesh/${kind}/Ed25519/v1\0`);
export async function sha(raw) { return hex(await crypto.subtle.digest('SHA-256',raw)); }
export async function sign(record,key,kind) { return {...unsigned(record),signature:hex(await crypto.subtle.sign('Ed25519',key,concat(domain(kind),typed(unsigned(record)))))}; }
export async function verify(record,publicHex,kind) {
  const key=await crypto.subtle.importKey('raw',unhex(publicHex,32),'Ed25519',false,['verify']);
  if (!await crypto.subtle.verify('Ed25519',key,unhex(record.signature,64),concat(domain(kind),typed(unsigned(record))))) throw Error('denied'); return record;
}
export function exact(value,keys) { if (!value || typeof value!=='object' || Array.isArray(value) || Object.keys(value).sort().join(',')!==[...keys].sort().join(',')) throw Error('invalid_request'); }
