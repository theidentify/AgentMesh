"""Python-authored typed/signature vectors; deterministic HPKE uses hpke-js's test hook."""
import json
import shutil
import subprocess
from pathlib import Path
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from agentmesh_log.encoding import typed,wire
from agentmesh_log.identity import sign, verify_signature
from agentmesh_log.registry_store import domain
from agentmesh_log.keys import SUITE,decode_ciphertext

GATEWAY=Path(__file__).resolve().parents[1]/'gateway'
VECTOR=Path(__file__).parent/'vectors/phase6/v1.json'


def require_node():
    if not shutil.which('node'): pytest.skip('Phase 6 cross-implementation requires node')
    if not (GATEWAY/'node_modules/@hpke/core').exists(): pytest.skip('Phase 6 cross-implementation requires gateway/node_modules')


def build():
    require_node(); private=Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    recipient=X25519PrivateKey.from_private_bytes(bytes(range(32,64)))
    value={'null':None,'bool':[True,False],'integer':42,'fraction':1.25,'ไทย':'สวัสดี','array':[1,'x']}
    request={'method':'POST','path':'/bridge','body_sha256':'00'*32,'ts':123456,'nonce':'11'*16,'app_id':'22'*16}
    record=sign({'format':'agentmesh-gateway-v1','gateway_public_key':private.public_key().public_bytes_raw().hex(),'origin':'https://example.workers.dev','issued_at':123456,'issuer_device':'33'*16},private,domain('gateway'))
    binding={'outbox_id':'00000000000000000000000001','app_id':'22'*16,'device_id':'33'*16}
    info=b'AgentMesh/outbox/v1\x00'+typed(binding); payload={'body':'synthetic vector memory','tags':[],'task':None,'label':None}; plain=wire(payload)
    result=subprocess.run(['node','tests/vector-seal.mjs','--seal'],cwd=GATEWAY,input=json.dumps({'public':recipient.public_key().public_bytes_raw().hex(),'info':info.hex(),'plain':plain.hex()}),text=True,encoding='utf-8',capture_output=True,timeout=30)
    if result.returncode: raise AssertionError('vector sealing failed')
    ciphertext=result.stdout
    assert SUITE.decrypt(decode_ciphertext(ciphertext),recipient,info)==plain
    return {'typed_value':value,'typed_hex':typed(value).hex(),'request':sign(request,private,domain('gateway-request')),'record':record,'public':private.public_key().public_bytes_raw().hex(),'outbox':{'binding':binding,'info':info.hex(),'payload':payload,'private':recipient.private_bytes_raw().hex(),'public':recipient.public_key().public_bytes_raw().hex(),'ciphertext':ciphertext}}


def test_p6_vectors():
    assert build()==json.loads(VECTOR.read_text(encoding='utf-8'))


if __name__=='__main__': VECTOR.write_text(json.dumps(build(),ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
