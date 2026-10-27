"""Encrypted device-local embedding cache keyed by keyed content digests.

Vectors are treated as content (embedding inversion): RAM-only SQLite,
serialized, sealed with a space-bound HKDF subkey of the index master key.
Never synced, backed up or exchanged; a lost or stale cache is rebuilt.
"""
import hashlib
import hmac
import os
import sqlite3
from array import array
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from ..encoding import typed
from ..storage.filesystem import _check
from .keystore import atomic_write

VERSION = 1


class VectorStore:
    def __init__(self, master_key, space):
        self.space = dict(space); self.aad = typed(self.space)
        okm = HKDF(algorithm=hashes.SHA256(), length=64, salt=None,
            info=b'AgentMesh/vector-cache/v1\x00'+self.aad).derive(master_key)
        self.key, self.mac_key = okm[:32], okm[32:]
        self._connect()

    @staticmethod
    def filename(space):
        return 'vectors-'+hashlib.sha256(typed(dict(space))).hexdigest()[:16]+'.snap'

    def _connect(self):
        self.connection = sqlite3.connect(':memory:', check_same_thread=False)
        self.connection.execute('PRAGMA temp_store=MEMORY')
        self.connection.execute('CREATE TABLE vectors(digest BLOB PRIMARY KEY, vector BLOB NOT NULL)')
        self.connection.execute('PRAGMA user_version=%d' % VERSION)
        self.dimension = None; self.dirty = False

    def digest(self, text):
        return hmac.new(self.mac_key, text.encode(), hashlib.sha256).digest()

    def get(self, text):
        row = self.connection.execute('SELECT vector FROM vectors WHERE digest=?', (self.digest(text),)).fetchone()
        return array('f', row[0]) if row else None

    def has(self, text): return self.get(text) is not None

    def put(self, text, vector):
        if self.dimension is None: self.dimension = len(vector)
        if len(vector) != self.dimension: raise ValueError('vector dimension mismatch')
        with self.connection:
            self.connection.execute('INSERT OR REPLACE INTO vectors VALUES (?,?)',
                (self.digest(text), array('f', vector).tobytes()))
        self.dirty = True

    def retain(self, texts):
        keep = {self.digest(t) for t in texts}
        stale = [r[0] for r in self.connection.execute('SELECT digest FROM vectors') if r[0] not in keep]
        with self.connection:
            self.connection.executemany('DELETE FROM vectors WHERE digest=?', [(d,) for d in stale])
        if stale: self.dirty = True

    def count(self): return self.connection.execute('SELECT count(*) FROM vectors').fetchone()[0]

    def save(self, path):
        nonce = os.urandom(12)
        atomic_write(path, nonce+ChaCha20Poly1305(self.key).encrypt(nonce, self.connection.serialize(), self.aad))
        self.dirty = False

    def load(self, path):
        try:
            _check(path); raw = path.read_bytes()
            self.connection.deserialize(ChaCha20Poly1305(self.key).decrypt(raw[:12], raw[12:], self.aad))
            self.connection.execute('PRAGMA temp_store=MEMORY')
            if self.connection.execute('PRAGMA user_version').fetchone()[0] != VERSION: raise ValueError('old cache')
            row = self.connection.execute('SELECT vector FROM vectors LIMIT 1').fetchone()
            self.dimension = len(array('f', row[0])) if row else None
            self.dirty = False
            return True
        except Exception:
            self.connection.close(); self._connect()
            return False
