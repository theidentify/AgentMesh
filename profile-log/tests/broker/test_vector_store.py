import os
import struct
import pytest
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from agentmesh_log.broker.vector_store import VectorStore

SPACE = dict(provider='fake', model='fake', revision='f'*64, metric='cosine')

def test_roundtrip_and_no_plaintext(tmp_path):
    s = VectorStore(b'k'*32, SPACE); s.put('migrate database', [0.6, 0.8]); path = tmp_path/'v.snap'
    s.save(path); raw = path.read_bytes()
    assert b'migrate' not in raw and struct.pack('f', 0.6) not in raw
    t = VectorStore(b'k'*32, SPACE); assert t.load(path)
    assert [round(x, 5) for x in t.get('migrate database')] == [0.6, 0.8] and t.get('other') is None

def test_wrong_key_space_or_tamper(tmp_path):
    s = VectorStore(b'k'*32, SPACE); s.put('a', [1.0, 0.0]); path = tmp_path/'v.snap'; s.save(path)
    assert not VectorStore(b'j'*32, SPACE).load(path)
    assert not VectorStore(b'k'*32, {**SPACE, 'revision': 'e'*64}).load(path)
    data = bytearray(path.read_bytes()); data[-1] ^= 1; path.write_bytes(bytes(data))
    t = VectorStore(b'k'*32, SPACE); assert not t.load(path) and t.get('a') is None

def test_keys_are_keyed_digests():
    a = VectorStore(b'k'*32, SPACE); b = VectorStore(b'j'*32, SPACE)
    assert a.digest('x') != b.digest('x') and len(a.digest('x')) == 32

def test_dimension_pinned_and_retain():
    s = VectorStore(b'k'*32, SPACE); s.put('a', [1.0, 0.0])
    with pytest.raises(ValueError, match='dimension'): s.put('b', [1.0, 0.0, 0.0])
    s.put('b', [0.0, 1.0]); s.retain({'b'})
    assert s.get('a') is None and s.get('b') is not None and s.count() == 1

def test_filename_is_space_specific():
    assert VectorStore.filename(SPACE) != VectorStore.filename({**SPACE, 'revision': 'e'*64})
    assert VectorStore.filename(SPACE).startswith('vectors-') and VectorStore.filename(SPACE).endswith('.snap')

def test_key_depends_on_space():
    a = VectorStore(b'k'*32, SPACE)
    b = VectorStore(b'k'*32, {**SPACE, 'revision': 'e'*64})
    assert a.key != b.key

def test_old_version_resets(tmp_path):
    s = VectorStore(b'k'*32, SPACE); s.put('a', [1.0, 0.0])
    s.connection.execute('PRAGMA user_version=2')
    nonce = os.urandom(12); path = tmp_path/'v.snap'
    path.write_bytes(nonce+ChaCha20Poly1305(s.key).encrypt(nonce, s.connection.serialize(), s.aad))
    t = VectorStore(b'k'*32, SPACE); t.put('existing', [0.0, 1.0])
    assert not t.load(path)
    assert t.count() == 0 and t.get('a') is None and t.get('existing') is None

def test_load_restores_dimension(tmp_path):
    s = VectorStore(b'k'*32, SPACE); s.put('a', [1.0, 0.0]); path = tmp_path/'v.snap'
    s.save(path)
    t = VectorStore(b'k'*32, SPACE); assert t.load(path)
    with pytest.raises(ValueError, match='dimension'): t.put('b', [1.0, 0.0, 0.0])

def test_separate_subkeys():
    s = VectorStore(b'k'*32, SPACE)
    assert s.key != s.mac_key
    assert len(s.key) == len(s.mac_key) == 32
