// Deterministic randomness hook stays test-only. Production never passes ekm.
import { CipherSuite, HkdfSha256 } from '@hpke/core';
import { DhkemX25519HkdfSha256 } from '@hpke/dhkem-x25519';
import { Chacha20Poly1305 } from '@hpke/chacha20poly1305';
import { Buffer } from 'node:buffer';
export async function vectorSeal(publicHex,info,plain) {
  const suite=new CipherSuite({kem:new DhkemX25519HkdfSha256(),kdf:new HkdfSha256(),aead:new Chacha20Poly1305()});
  const publicKey=await suite.kem.importKey('raw',Buffer.from(publicHex,'hex'));
  const sender=await suite.createSenderContext({recipientPublicKey:publicKey,info:Buffer.from(info,'hex'),ekm:new Uint8Array(32).fill(42)});
  return Buffer.concat([Buffer.from(sender.enc),Buffer.from(await sender.seal(Buffer.from(plain,'hex')))]).toString('base64url');
}
if(process.argv.includes('--seal')) {
  const chunks=[];for await(const chunk of process.stdin)chunks.push(chunk);
  const request=JSON.parse(Buffer.concat(chunks).toString('utf8'));
  process.stdout.write(await vectorSeal(request.public,request.info,request.plain));
}
