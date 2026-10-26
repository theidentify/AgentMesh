# Broker Semantic/Hybrid Recall Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in `hybrid` recall mode to the profile-log broker: the existing FTS5 keyword ranking fused (RRF k=60) with cosine ranking over bge-m3 embeddings from **one central Ollama on the Mac Air**. The Air uses loopback. The Mac and Windows reach it through a dedicated Cloudflare Tunnel with Access Service Auth. Vectors are stored only in an encrypted, device-local cache.

**Provider decision (owner, 2026-10-26):** use the central Ollama over the existing Cloudflare Tunnel/Access setup. Rejected: OpenRouter (plaintext memory to a third party plus its upstream provider) and Ollama on every device (install burden). One host means one model digest, so all devices produce vectors in the same space.

**Architecture:** A new `broker/semantic.py` owns the Ollama provider, an encrypted `VectorStore`, and a background backfill thread. `AgentIndex.recall` gains an optional `semantic` argument. When the argument is present, the broker selects the agent's authorized rows without a text filter, ranks them by cosine and fuses that ranking with the unchanged keyword ranking. Keyword mode stays byte-identical. Vectors never leave the device and are not synced, backed up or exchanged. On disk they get the same protection as index snapshots: an HKDF subkey of `index_master_key` with ChaCha20-Poly1305.

**Tech Stack:** Python 3.11 stdlib (`http.client`, `ssl`, `array`, `hmac`, `sqlite3`), `cryptography` (already a dependency), Ollama `bge-m3:latest` (1024-dim) on the Mac Air, Cloudflare Tunnel + Access (Service Auth) for remote devices. No NumPy or hnswlib. At this corpus size exact pure-Python cosine is fast enough (see measurements).

---

## Measured on the Mac (2026-10-26, M-series, bge-m3 F16 in Ollama)

The host moved to the **Mac Air** (owner, 2026-10-26). These numbers come from the Mac and are a reference only. Task 9 re-measures them on the Air as host, and on the Mac/Windows as tunnel clients. The Air's backfill and cold-load numbers may be slower (passive cooling). The Mac's own recalls now also pay the tunnel round trip.

| Item | Measured | Effect |
|---|---|---|
| Current keyword recall, end to end through memory-sync CLI | 0.23–0.55 s | baseline |
| Query embed, model warm | median 29 ms, max 34 ms | +~30 ms per recall |
| Query embed, model cold (Ollama unloaded it) | ~2.4 s | must not block: prefetch outside lock + 4 s timeout + keyword degrade + `keep_alive` |
| Cosine, pure Python, 1024-dim | 1k rows 21 ms · 2k 44 ms · 5k 108 ms | +20–110 ms |
| Document backfill | ~21 docs/s (batch 32) | ~1.5 min per 2k rows, once, background thread |
| Ollama resident memory while loaded | ~670 MB | released after `keep_alive` expires |
| Cache size | 4 KB/vector (float32 × 1024) | 2k rows ≈ 8 MB encrypted file |

For remote devices, add the tunnel round trip (estimated 50–150 ms per query embed, to be measured in Task 9) and the backfill upload. The first HTTPS connect is expensive because of the owner's broken Wi-Fi IPv6 (~10 s per new connection), so the adapter connects to IPv4 first and keeps one persistent connection.

Expected warm hybrid overhead on the host: **+60–150 ms** on top of 0.23–0.55 s. That is acceptable. Keyword mode has **zero** overhead because none of this code runs.

**Performance gates (Task 9):** warm hybrid p95 ≤ keyword p95 + 250 ms on the live Mac corpus. Cold model: recall returns within `timeout_seconds` by degrading to keyword. Backfill must not raise the p95 of concurrent keyword recall by more than 50 ms.

## Security and privacy design (decisions)

1. **Vectors are treated as content.** Embedding-inversion attacks can recover much of the text, so vectors get the same protection as the snapshot. Encryption: ChaCha20-Poly1305 under `HKDF(index_master_key, info="AgentMesh/vector-cache/v1\0" + typed(space))`, where `space` = provider/model/revision(Ollama digest)/metric. The AAD is `typed(space)`. A model change derives a new key and a new file, so old vectors can never be mixed with new ones.
2. **Cache keys are `HMAC-SHA256(mac_subkey, content)`, not plain SHA-256.** Plain hashes would let anyone who can read the file confirm a guessed content. The HMAC key comes from the same HKDF output.
3. **Plaintext never touches disk.** The cache is an in-memory SQLite DB (`temp_store=MEMORY`) that is serialized → encrypted → `atomic_write` (0600), the same pattern as `AgentIndex.save`.
4. **Authorization is unchanged.** One shared cache per device and space, keyed by content. Ranking only uses rows already selected from that agent's own `AgentIndex` (grant-filtered) with the request's scope/profile/project filter. This meets the "caller MUST authorize candidates" contract in `broker/vectors.py`.
5. **Secret-label content is never sent to Ollama by default.** `semantic.embed_labels` defaults to `["general","internal"]`. Secret rows stay keyword-only. Query text and eligible content *are* sent to the central Ollama: over loopback on the Air, and through the tunnel from the Mac and Windows.
8. **Remote transport = Cloudflare Tunnel + Access, the same trust model as `bridge.krisb8.com`.** TLS terminates at Cloudflare's edge, so Cloudflare can see embedded text and queries in plaintext. That trust is already granted for recall results through the MCP gateway, and this plan accepts it the same way. If end-to-end protection is needed later, use Tailscale/WireGuard instead; the adapter only needs a different `endpoint`.
9. **Do not expose all of Ollama.** Ollama has no auth, and its API includes `/api/pull`, `/api/delete` and `/api/generate`. Use a dedicated tunnel `agentmesh-embed-air` with its own connector on the Air (same pattern as `agentmesh-insight-mac`). Its ingress rule matches only path `^/api/(embed|tags)$` → `http://127.0.0.1:11434`, and everything else gets `http_status:404`. The Access application allows only a Service Auth policy with service token `agentmesh-embed`, with no identity login. Ollama keeps listening on 127.0.0.1.
10. **The service token lives in the OS credential store only.** On macOS that is the Keychain service `org.agentmesh.embed`, accounts `client-id`/`client-secret`; on Windows it is wincred. `settings.local.json` holds only the reference `keychain:org.agentmesh.embed`. The token is never kept in a plist, in settings or in logs. The adapter sends `CF-Access-Client-Id`/`CF-Access-Client-Secret` plus a non-default `User-Agent`, because Cloudflare 1010 blocks Python-urllib's UA.
11. **Availability:** the Air is a laptop, so expect it to sleep. Whenever it sleeps, is closed or is offline, the other devices degrade to keyword and report `degraded: provider_unavailable`. A connection that fails is never retried in a loop: the lazy reconnect runs at most once a minute.
6. **Not synced, not backed up.** `index/` is outside `REPLICA_PREFIXES` and `backup.py` only backs up keystore state. The cache can be rebuilt, which keeps it out of the consent-gated backups.
7. **Ollama boundary (documented, not solved):** Ollama runs as the same user, and `OLLAMA_DEBUG` logs may contain inputs. The model tag must stay frozen. The digest is rechecked around each batch, and the check does not catch an ABA swap (same caveat as `profile-memory/CONTRACT.md`).

## File structure

- Modify `profile-log/agentmesh_log/storage/r2creds.py`: `kind='access'` for Cloudflare Access service tokens.
- Create `profile-log/agentmesh_log/broker/embedder.py`: port of `profile-memory/agentmesh_memory/embeddings.py` with `keep_alive`, an HTTPS+Access remote endpoint and a persistent IPv4-first connection added. Only the `OllamaEmbedding` class; imports `validate_vectors` from `.vectors`.
- Create `profile-log/agentmesh_log/broker/vector_store.py`: encrypted cache (HKDF/HMAC/AEAD, get/put/retain/save/load).
- Create `profile-log/agentmesh_log/broker/semantic.py`: `Semantic` (settings, lazy provider, query LRU, `fuse`, `backfill`, `save`).
- Modify `profile-log/agentmesh_log/broker/index.py`: split recall into candidate builders with a `textual` switch; optional `semantic`.
- Modify `profile-log/agentmesh_log/broker/service.py`: construct `Semantic`, `mode` field, prefetch outside the lock, backfill thread, save in `flush_indexes`.
- Modify `profile-log/agentmesh_log/settings.py`: validate the `semantic` section.
- Modify `profile-log/agentmesh_log/cli.py`: `recall --mode`.
- Tests: `profile-log/tests/broker/support.py` (`FakeEmbedding`), `test_vector_store.py`, `test_semantic.py`, `test_index.py`, `test_service.py`, `profile-log/tests/test_settings.py` (or the existing settings test file), `memory-sync/test_profile_log_bridge.py`.
- Owner-run: `profile-log/tests/broker/eval_semantic.py`.
- Docs: `profile-log/README.md`, `docs/use/recall.md`, `docs/privacy.md`.

Run all profile-log tests with:
`python -m pytest -q -rs -p no:cacheprovider profile-log/tests` (from the worktree root, using a Python 3.11 venv with `profile-log/requirements-dev.txt`).

---

### Task 1: Test fake embedding provider

**Files:**
- Modify: `profile-log/tests/broker/support.py`

- [ ] **Step 1: Add a deterministic offline provider.** Words in one synonym group map to the same axis, so Thai/English cross-lingual hits can be tested without Ollama.

```python
class FakeEmbedding:
    """Offline stand-in for OllamaEmbedding: synonym groups share one axis."""
    GROUPS = (('database','db','ฐานข้อมูล'), ('deploy','rollout','ติดตั้ง'), ('launchd','service'))
    def __init__(self, revision='f'*64, fail=False):
        self.space = dict(provider='fake', model='fake', revision=revision, metric='cosine')
        self.calls = []; self.fail = fail
    def embed(self, texts):
        if self.fail: raise RuntimeError('local Ollama transport failed; no fallback')
        self.calls.append(list(texts)); out = []
        for text in texts:
            v = [0.0]*(len(self.GROUPS)+1); low = text.lower()
            for i, group in enumerate(self.GROUPS):
                v[i] = float(sum(low.count(w) for w in group))
            v[-1] = 0.1  # never a zero vector
            norm = sum(x*x for x in v) ** .5; out.append([x/norm for x in v])
        return out
```

- [ ] **Step 2: Commit**

```bash
git add profile-log/tests/broker/support.py
git commit -m "test(broker): offline fake embedding provider"
```

### Task 2: Port the Ollama adapter

**Files:**
- Create: `profile-log/agentmesh_log/broker/embedder.py`
- Test: `profile-log/tests/broker/test_embedder.py`

- [ ] **Step 1: Copy the adapter.** Copy `profile-memory/agentmesh_memory/embeddings.py` to the new path and make these exact edits:
  - drop the `EmbeddingProvider` Protocol and its `typing` import;
  - keep `from .vectors import validate_vectors` unchanged (`broker/vectors.py` is byte-identical to the profile-memory module, as the Phase 3 report confirms);
  - add `keep_alive='30m'` to `__init__` (validate `re.fullmatch(r'\d{1,4}[smh]', keep_alive)`) and send it in the `/api/embed` payload: `{'model': self.model, 'input': texts, 'truncate': False, 'keep_alive': self._keep_alive}`;
  - make `space` return `{'provider':'ollama','model':self.model,'revision':self._revision,'metric':'cosine'}`, without `dimension` (the store pins dimension itself).
  - Edit the docstring's first line to: `"""Ollama embedding adapter (loopback, or HTTPS behind Cloudflare Access), ported from profile-memory (Phase 8)."""`

  - **Remote endpoint (central Ollama via Access):** extend `__init__` with `headers=None` and accept two endpoint forms:
    - `http://<literal loopback>[:port]`: unchanged, `headers` must be `None`;
    - `https://<dns-name>[:port]`: requires `headers` to be exactly `{'CF-Access-Client-Id': ..., 'CF-Access-Client-Secret': ...}`. The hostname must match `[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+` (lowercase only), with no userinfo, path, query or fragment.
    Every request sends `{'Content-Type':'application/json','User-Agent':'AgentMesh-embed/1', **headers}`.
  - Replace the per-request connection with **one persistent connection** guarded by `threading.Lock`. If a send fails with `OSError`/`HTTPException` on a reused connection, close it and retry **once** on a fresh connection; a second failure raises `RuntimeError('Ollama transport failed; no fallback')`. If the 8 MiB budget is exceeded, close the connection before raising. Status `302`/`401`/`403` raises `RuntimeError('embedding endpoint refused credentials')`; that is Access rejecting the call, and there is no redirect following. Any other non-200 status keeps the existing error.
  - HTTPS uses this IPv4-first connection (the owner's Wi-Fi IPv6 stalls ~10 s per connect) and `ssl.create_default_context()`:

```python
class _IPv4FirstHTTPS(http.client.HTTPSConnection):
    """Try IPv4 addresses before IPv6; certificate and hostname checks unchanged."""
    def connect(self):
        infos = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM)
        infos.sort(key=lambda info: info[0] != socket.AF_INET)
        error = OSError('no address')
        for family, kind, proto, _, address in infos:
            sock = socket.socket(family, kind, proto)
            try:
                sock.settimeout(self.timeout); sock.connect(address); break
            except OSError as exc:
                error = exc; sock.close()
        else:
            raise error
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
```

- [ ] **Step 2: Write tests with a stub HTTP layer** (no network):

```python
import pytest
from agentmesh_log.broker import embedder

DIGEST = 'a'*64

def stub(monkeypatch, digests, vectors):
    seq = iter(digests)
    def request(self, path, payload=None):
        if path == '/api/tags': return {'models': [{'name': 'bge-m3:latest', 'digest': next(seq)}]}
        assert payload['keep_alive'] == '30m' and payload['truncate'] is False
        return {'model': 'bge-m3:latest', 'embeddings': vectors}
    monkeypatch.setattr(embedder.OllamaEmbedding, '_request', request)

def test_embed_normalizes_and_pins_space(monkeypatch):
    stub(monkeypatch, [DIGEST]*3, [[3.0, 4.0]])
    e = embedder.OllamaEmbedding()
    assert e.embed(['x']) == [[pytest.approx(0.6), pytest.approx(0.8)]]
    assert e.space == dict(provider='ollama', model='bge-m3:latest', revision=DIGEST, metric='cosine')

def test_revision_change_discards_batch(monkeypatch):
    stub(monkeypatch, [DIGEST, DIGEST, 'b'*64], [[1.0, 0.0]])
    with pytest.raises(RuntimeError, match='revision changed'): embedder.OllamaEmbedding().embed(['x'])

@pytest.mark.parametrize('url', ['http://10.0.0.1:11434', 'http://localhost:11434', 'ftp://127.0.0.1'])
def test_http_is_literal_loopback_only(url):
    with pytest.raises(ValueError): embedder.OllamaEmbedding(base_url=url)

ACCESS = {'CF-Access-Client-Id': 'a'*32+'.access', 'CF-Access-Client-Secret': 'b'*64}

@pytest.mark.parametrize('url,headers', [
    ('https://embed.example.com', None),                         # remote needs Access headers
    ('https://embed.example.com/api', ACCESS),                   # no path
    ('https://user@embed.example.com', ACCESS),                  # no userinfo
    ('https://Embed.Example.com', ACCESS),                       # lowercase DNS only
    ('http://127.0.0.1:11434', ACCESS),                          # loopback takes no headers
    ('https://embed.example.com', {'CF-Access-Client-Id': 'x'}), # exact header set
])
def test_remote_endpoint_rules(url, headers):
    with pytest.raises(ValueError): embedder.OllamaEmbedding(base_url=url, headers=headers)

class FakeResponse:
    def __init__(self, status, body): self.status, self.body = status, body
    def read(self, n): return self.body[:n]

class FakeConnection:
    instances = []
    def __init__(self, *a, **kw): self.sent = []; self.closed = False; FakeConnection.instances.append(self)
    def request(self, method, path, body=None, headers=None): self.sent.append((path, headers))
    def getresponse(self): return FakeConnection.replies.pop(0)
    def close(self): self.closed = True

def remote(monkeypatch, replies):
    import json
    FakeConnection.instances = []
    FakeConnection.replies = [FakeResponse(s, json.dumps(b).encode()) for s, b in replies]
    monkeypatch.setattr(embedder, '_IPv4FirstHTTPS', FakeConnection)
    return embedder.OllamaEmbedding(base_url='https://embed.example.com', headers=ACCESS)

TAGS = {'models': [{'name': 'bge-m3:latest', 'digest': DIGEST}]}

def test_remote_sends_access_headers_and_reuses_connection(monkeypatch):
    e = remote(monkeypatch, [(200, TAGS), (200, TAGS), (200, {'model': 'bge-m3:latest', 'embeddings': [[1.0, 0.0]]}), (200, TAGS)])
    e.embed(['x'])
    assert len(FakeConnection.instances) == 1
    headers = FakeConnection.instances[0].sent[0][1]
    assert headers['CF-Access-Client-Id'] == ACCESS['CF-Access-Client-Id'] and headers['User-Agent'] == 'AgentMesh-embed/1'

@pytest.mark.parametrize('status', [302, 401, 403])
def test_access_refusal_is_not_followed(monkeypatch, status):
    with pytest.raises(RuntimeError, match='refused credentials'): remote(monkeypatch, [(status, {})])
```

  The two stub-based tests at the top (`stub(...)`) patch `_request` directly and stay valid.

- [ ] **Step 3: Run** `python -m pytest -q profile-log/tests/broker/test_embedder.py`. Expected: 15 passed.
- [ ] **Step 4: Commit**

```bash
git add profile-log/agentmesh_log/broker/embedder.py profile-log/tests/broker/test_embedder.py
git commit -m "feat(broker): port local Ollama embedding adapter"
```

### Task 2b: Access service-token credentials from the OS store

**Files:**
- Modify: `profile-log/agentmesh_log/storage/r2creds.py`
- Test: the existing r2creds tests (`git grep -l load_keychain_credentials profile-log/tests`) plus new cases there

- [ ] **Step 1: Write the failing tests.** Reuse the existing runner/api fakes in that test file. Add these cases:
  - `load_credentials('keychain:org.agentmesh.embed', kind='access', runner=fake)` returns `('a'*32+'.access', 'b'*64)` when the fake returns those for accounts `client-id`/`client-secret`;
  - a client id without `.access`, or with uppercase hex, raises `StorageError('embedding credentials refused')`;
  - Keychain exit code 44 raises `StorageError('embedding credentials missing')`;
  - the default `kind='s3'` behavior, and its existing `'S3 credentials …'` messages, stay unchanged (the existing tests must still pass);
  - on Windows (the api fake), `WindowsR2Credentials(api, kind='access').load(svc)` reads `svc/client-id` (blob size 39) and `svc/client-secret` (64).
- [ ] **Step 2: Implement** by turning the hard-coded account table into a parameter:

```python
KINDS = {
    's3': ('S3', (('access-key-id', 32, '[0-9a-f]{32}'), ('secret-access-key', 64, '[0-9a-f]{64}'))),
    'access': ('embedding', (('client-id', 39, r'[0-9a-f]{32}\.access'), ('client-secret', 64, '[0-9a-f]{64}'))),
}
```

  - `load_keychain_credentials(service, runner=..., keychain=None, kind='s3')`: `label, accounts = KINDS[kind]`. Loop over `for account, size, pattern in accounts`, check `re.fullmatch(pattern, value)`, and build messages as `f'{label} credentials refused'` / `f'{label} credentials missing'`.
  - `load_credentials(ref, *, runner=..., api=None, kind='s3')` passes `kind` through. `WindowsR2Credentials.__init__(self, api=None, kind='s3')` does the same, and its `load`/`store`/`delete` iterate `accounts` and check `CredentialBlobSize != size`.
  - `credentials_command(action, ref, *, getpass_fn=None, api=None, kind='s3')`: for `kind='access'`, prompt `'Access client ID: '` / `'Access client secret: '`.
  Unknown `kind` raises `ValueError`.
- [ ] **Step 3: Run** `python -m pytest -q profile-log/tests -k "r2creds or credentials"`. Expected: all pass, old and new.
- [ ] **Step 4: Commit** (Claude commits) `feat(storage): load Access service tokens from OS credential stores`

### Task 3: Encrypted vector store

**Files:**
- Create: `profile-log/agentmesh_log/broker/vector_store.py`
- Test: `profile-log/tests/broker/test_vector_store.py`

- [ ] **Step 1: Write the failing tests**

```python
import struct
import pytest
from agentmesh_log.broker.vector_store import VectorStore

SPACE = dict(provider='fake', model='fake', revision='f'*64, metric='cosine')

def test_roundtrip_and_no_plaintext(tmp_path):
    s = VectorStore(b'k'*32, SPACE); s.put('migrate database', [0.6, 0.8]); path = tmp_path/'v.snap'
    s.save(path); raw = path.read_bytes()
    assert b'migrate' not in raw and struct.pack('f', 0.6) not in raw
    t = VectorStore(b'k'*32, SPACE); assert t.load(path)
    assert [round(x, 5) for x in t.get('migrate database')] == [0.6, 0.8] and t.get('other') is None

def test_wrong_key_space_or_tamper_rebuilds(tmp_path):
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
```

- [ ] **Step 2: Run** `python -m pytest -q profile-log/tests/broker/test_vector_store.py`. Expected: FAIL with `ModuleNotFoundError: agentmesh_log.broker.vector_store`.

- [ ] **Step 3: Implement**

```python
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
```

- [ ] **Step 4: Run** the test file again. Expected: 5 passed.
- [ ] **Step 5: Commit**

```bash
git add profile-log/agentmesh_log/broker/vector_store.py profile-log/tests/broker/test_vector_store.py
git commit -m "feat(broker): encrypted device-local vector cache"
```

### Task 4: Split AgentIndex recall into candidate builders (no behavior change)

**Files:**
- Modify: `profile-log/agentmesh_log/broker/index.py` (`recall`, `recall_compat`)
- Test: `profile-log/tests/broker/test_index.py`

- [ ] **Step 1: Write a characterization test first**, so the refactor is checked against the old output:

```python
def test_recall_output_unchanged_by_refactor():
    a = AgentIndex('a', b'a'*32)
    a.replace([row(), {**row('b'),'content':'database only'}, {**row('c'),'content':'unrelated'}])
    plan = analyze_query('migrate database')
    for compat in (False, True):
        out = a.recall(plan, 12, {'work/a'}, {'work'}, compat=compat)
        assert set(out) == {'query','query_plan','candidate_counts','results','evidence'}
        assert 'retrieval' not in out['query_plan']
        assert [r['id'] for r in out['results']] == ['a']
```

Run it before refactoring: `python -m pytest -q profile-log/tests/broker/test_index.py`. Expected: PASS on current code. Also record a baseline: `python -m pytest -q profile-log/tests memory-sync/test_profile_log_bridge.py memory-sync/test_profile_log_end_to_end.py memory-sync/test_phase7_recall.py` (all pass).

- [ ] **Step 2: Refactor.** Replace `recall` and `recall_compat` with the code below. The SQL and shaping are moved verbatim. `textual=False` replaces each text predicate with `1`, and `cap=None` drops the per-table chronological cap.

```python
    def recall(self, plan, limit, scopes, profiles, compat=False, semantic=None):
        build=self._compat_candidates if compat else self._candidates
        candidates=build(plan,scopes,profiles,cap=limit)
        selected=[r for r in candidates if (compat and r['source_type']=='summary')
            or len(plan.terms)<2 or plan.task_refs or r['matched_terms']>=2]
        query_plan={k:list(v) if isinstance(v,tuple) else v for k,v in asdict(plan).items()}
        if semantic is not None:
            selected,query_plan['retrieval']=semantic.fuse(plan,selected,
                lambda:build(plan,scopes,profiles,cap=None,textual=False),limit)
        if compat:
            counts=dict(durable=sum(r['source_type']=='durable' for r in candidates),
                summaries=sum(r['source_type']=='summary' for r in candidates),events=0)
        else: counts=dict(durable=len(candidates),summaries=0,events=0)
        return {'query':plan.query,'query_plan':query_plan,'candidate_counts':counts,
            'results':selected[:limit],'evidence':[]}

    def _candidates(self, plan, scopes, profiles, *, cap=None, textual=True):
        matches=[]; args=[]
        if textual:
            if plan.terms:
                match=' OR '.join('"'+t.replace('"','""')+'"' for t in plan.terms)
                matches.append('rowid IN (SELECT rowid FROM search WHERE search MATCH ?)'); args.append(match)
            for term in dict.fromkeys((plan.query.lower(),*plan.terms,*(t.lower() for t in plan.task_refs))):
                if term: matches.append('instr(folded,?)>0'); args.append(term)
            if plan.task_refs:
                matches.append('task_ref IN (SELECT value FROM json_each(?))'); args.append(wire(list(plan.task_refs)).decode())
        text=('('+(' OR '.join(matches) or '0')+')') if textual else '1'
        sql='SELECT data FROM metadata WHERE '+text
        sql+=' AND scope IN (SELECT value FROM json_each(?)) AND profile IN (SELECT value FROM json_each(?))'
        args.extend((wire(sorted(scopes)).decode(),wire(sorted(profiles)).decode()))
        if plan.project is not None: sql+=' AND project=?'; args.append(plan.project)
        # These rows were validated on insertion, or authenticated on snapshot load.
        # Decode only SQL-selected candidates; do not re-run wire validation per query.
        candidates=[_decode_row(r[0]) for r in self.connection.execute(sql,args)]
        for row in candidates:
            row.pop('_outbox',None)
            row.pop('_migration',None)
            _score(row,plan)
        candidates.sort(key=lambda r:(r['score'],r['updated_at'],r['id']), reverse=True)
        return candidates
```

For `_compat_candidates(self, plan, scopes, profiles, *, cap=None, textual=True)`: move the body of the current `recall_compat` unchanged from `matches=[]; args=[]` through the `_score(row,plan)` loop and the `candidates.sort(...)` line, then `return candidates`. Apply exactly these edits to it:
  - wrap the `for term ...` loop and the `if plan.task_refs:` match in `if textual:`;
  - `text='('+(' OR '.join(matches) or '0')+')' if textual else '1'`;
  - `legacy=text.replace('upper(scope_key)','task_ref') if textual else '1'`, and guard `if plan.terms:` (the FTS legacy match) with `if textual and plan.terms:`;
  - replace `[:limit]` in the chronology line with `[:cap]` (slicing with `None` keeps all rows).
  Delete the old `recall_compat` method and change no other line.

- [ ] **Step 3: Run** the full baseline command from Step 1. Expected: the same pass count as the baseline, with zero failures.
- [ ] **Step 4: Commit**

```bash
git add profile-log/agentmesh_log/broker/index.py profile-log/tests/broker/test_index.py
git commit -m "refactor(broker): split recall into candidate builders"
```

### Task 5: Semantic ranking and RRF fusion

**Files:**
- Create: `profile-log/agentmesh_log/broker/semantic.py`
- Test: `profile-log/tests/broker/test_semantic.py`

- [ ] **Step 1: Write the failing tests**

```python
from agentmesh_log.broker.index import AgentIndex
from agentmesh_log.broker.query import analyze_query
from agentmesh_log.broker.semantic import Semantic
from .support import FakeEmbedding
from .test_index import row

def make(tmp_path, **kw):
    s = Semantic(tmp_path, b'k'*32, mode='hybrid', provider=FakeEmbedding(**kw), min_score=0.5)
    return s

def corpus():
    a = AgentIndex('a', b'a'*32)
    a.replace([row('kw'), {**row('th'),'content':'ย้ายฐานข้อมูลไปเครื่องใหม่'},
        {**row('no'),'content':'launchd service reload'}, {**row('sec'),'content':'database password','label':'secret'}])
    return a

def test_semantic_only_hit_is_fused(tmp_path):
    a = corpus(); s = make(tmp_path); s.backfill_rows(a.rows())
    out = a.recall(analyze_query('migrate database'), 12, {'work/a'}, {'work'}, semantic=s)
    ids = [r['id'] for r in out['results']]
    assert ids[0] == 'kw' and 'th' in ids and 'no' not in ids and 'sec' not in ids
    th = next(r for r in out['results'] if r['id'] == 'th')
    assert th['ranking']['keyword_rank'] is None and th['ranking']['semantic_rank'] >= 1
    assert out['query_plan']['retrieval']['effective'] == 'hybrid'

def test_secret_never_sent_to_provider(tmp_path):
    a = corpus(); s = make(tmp_path); s.backfill_rows(a.rows())
    assert all('password' not in t for call in s.provider.calls for t in call)

def test_provider_down_degrades_to_keyword(tmp_path):
    a = corpus(); s = make(tmp_path, fail=True)
    out = a.recall(analyze_query('migrate database'), 12, {'work/a'}, {'work'}, semantic=s)
    assert [r['id'] for r in out['results']] == ['kw']
    assert out['query_plan']['retrieval'] == dict(requested='hybrid', effective='keyword', degraded='provider_unavailable')

def test_missing_vectors_reported_not_guessed(tmp_path):
    a = corpus(); s = make(tmp_path)  # no backfill
    out = a.recall(analyze_query('migrate database'), 12, {'work/a'}, {'work'}, semantic=s)
    assert [r['id'] for r in out['results']] == ['kw'] and out['query_plan']['retrieval']['coverage']['missing'] == 3

def test_authorization_is_the_index_filter(tmp_path):
    a = corpus(); s = make(tmp_path); s.backfill_rows(a.rows())
    out = a.recall(analyze_query('ฐานข้อมูล'), 12, {'work/b'}, {'work'}, semantic=s)
    assert out['results'] == []

def test_save_load_cache(tmp_path):
    a = corpus(); s = make(tmp_path); s.backfill_rows(a.rows()); s.save()
    t = make(tmp_path); assert t.store.count() == 3
    assert not any(p.name.endswith('.tmp') for p in (tmp_path).iterdir())
```

- [ ] **Step 2: Run** `python -m pytest -q profile-log/tests/broker/test_semantic.py`. Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

```python
"""Opt-in hybrid recall: keyword ranks fused with cosine ranks (RRF k=60).

Candidates are the caller's AgentIndex rows (already grant/scope filtered).
Provider failure degrades a request to keyword and says so; it never guesses.
"""
from array import array
from collections import OrderedDict
from operator import mul
import threading
import time
from .vector_store import VectorStore

K = 60
MAX_TEXT = 32768
BATCH_CHARS = 120000
BATCH_ITEMS = 32


class Semantic:
    def __init__(self, index_dir, master_key, *, mode='hybrid', model='bge-m3:latest',
                 embed_labels=('general','internal'), min_score=0.45, provider=None, factory=None):
        self.index_dir, self.master_key, self.mode = index_dir, master_key, mode
        self.model, self.labels, self.min_score = model, frozenset(embed_labels), min_score
        self._factory = factory; self._next_attempt = 0.0
        self._queries = OrderedDict(); self._lock = threading.Lock()
        self.provider = self.store = None
        if provider is not None: self._attach(provider)

    @classmethod
    def from_settings(cls, index_dir, master_key, section):
        if not section or section.get('mode', 'keyword') == 'keyword': return None
        from .embedder import OllamaEmbedding
        model = section.get('model', 'bge-m3:latest')
        def factory():
            if 'endpoint' not in section: return OllamaEmbedding(model=model, timeout=4)
            from ..storage.r2creds import load_credentials
            from ..storage import StorageError
            try: client_id, secret = load_credentials(section['credentials'], kind='access')
            except StorageError as exc: raise RuntimeError(str(exc)) from None
            return OllamaEmbedding(model=model, base_url=section['endpoint'], timeout=4,
                headers={'CF-Access-Client-Id': client_id, 'CF-Access-Client-Secret': secret})
        return cls(index_dir, master_key, mode=section['mode'], model=model,
            embed_labels=section.get('embed_labels', ('general','internal')),
            min_score=section.get('min_score', 0.45), factory=factory)

    def _attach(self, provider):
        self.provider = provider
        self.store = VectorStore(self.master_key, provider.space)
        self.path = self.index_dir/VectorStore.filename(provider.space)
        self.store.load(self.path)
        for stale in self.index_dir.glob('vectors-*.snap'):
            if stale != self.path: stale.unlink()

    def ensure(self):
        """Connect lazily; Ollama may start after the broker. Retry at most once a minute."""
        if self.provider is not None or self._factory is None: return self.provider is not None
        if time.monotonic() < self._next_attempt: return False
        self._next_attempt = time.monotonic()+60
        try: self._attach(self._factory()); return True
        except (RuntimeError, ValueError): return False

    def query_vector(self, text):
        with self._lock:
            if text in self._queries: self._queries.move_to_end(text); return self._queries[text]
        if not self.ensure(): raise RuntimeError('provider unavailable')
        vector = array('f', self.provider.embed([text])[0])
        with self._lock:
            self._queries[text] = vector
            while len(self._queries) > 256: self._queries.popitem(last=False)
        return vector

    def prefetch(self, text):
        try: self.query_vector(text)
        except (RuntimeError, ValueError): pass

    def _eligible(self, row):
        return row.get('label') in self.labels and 0 < len(row['content']) <= MAX_TEXT

    def fuse(self, plan, keyword, authorized, limit):
        info = dict(requested=self.mode, effective=self.mode)
        try: query = self.query_vector(plan.query)
        except (RuntimeError, ValueError):
            self.provider = None if self._factory else self.provider
            return keyword, {**info, 'effective': 'keyword', 'degraded': 'provider_unavailable'}
        scored = []; missing = 0
        for row in authorized():
            if not self._eligible(row): continue
            vector = self.store.get(row['content'])
            if vector is None or len(vector) != len(query): missing += 1; continue
            cosine = sum(map(mul, query, vector))
            if cosine >= self.min_score: scored.append((cosine, row))
        scored.sort(key=lambda item: (-item[0], item[1]['id']))
        semantic = scored[:max(limit*4, 50)]
        info['coverage'] = dict(ranked=len(scored), missing=missing)
        entries = {}
        for field, listing in (('keyword_rank', keyword), ('semantic_rank', [r for _, r in semantic])):
            for rank, row in enumerate(listing, 1):
                entry = entries.setdefault(row['id'], {'row': row, 'keyword_rank': None, 'semantic_rank': None})
                entry[field] = rank
        cosines = {row['id']: cosine for cosine, row in semantic}
        fused = []
        for identifier, entry in entries.items():
            rrf = sum(1/(K+entry[f]) for f in ('keyword_rank','semantic_rank') if entry[f] is not None)
            row = entry['row']
            row['ranking'] = dict(keyword_rank=entry['keyword_rank'], semantic_rank=entry['semantic_rank'],
                cosine=round(cosines[identifier], 4) if identifier in cosines else None, rrf=round(rrf, 6))
            fused.append(row)
        fused.sort(key=lambda r: (-r['ranking']['rrf'], -r['score'], r['id']))
        return fused, info

    def pending(self, rows, budget=256):
        texts = [r['content'] for r in rows if self._eligible(r)]
        return [t for t in dict.fromkeys(texts) if not self.store.has(t)][:budget]

    def embed_texts(self, texts):
        """Provider calls only; caller stores results. Greedy char-bounded batches."""
        out = []; batch = []; size = 0
        for text in texts + [None]:
            if text is None or len(batch) == BATCH_ITEMS or size+len(text) > BATCH_CHARS:
                if batch: out.extend(zip(batch, self.provider.embed(batch)))
                batch = []; size = 0
            if text is not None: batch.append(text); size += len(text)
        return out

    def backfill_rows(self, rows):
        """Single-threaded helper for tests and the CLI: embed and store in one go."""
        if not self.ensure(): return 0
        pairs = self.embed_texts(self.pending(rows))
        for text, vector in pairs: self.store.put(text, vector)
        return len(pairs)

    def save(self, live_texts=None):
        if self.store is None: return
        if live_texts is not None: self.store.retain(live_texts)
        if self.store.dirty: self.store.save(self.path)
```

Note: `test_secret_never_sent_to_provider` and `test_missing_vectors...` count three eligible rows (`kw`, `th`, `no`) because `sec` is excluded by label.

- [ ] **Step 4: Run** the test file. Expected: 6 passed. Then run `python -m pytest -q profile-log/tests/broker`. Expected: all pass.
- [ ] **Step 5: Commit**

```bash
git add profile-log/agentmesh_log/broker/semantic.py profile-log/tests/broker/test_semantic.py
git commit -m "feat(broker): hybrid RRF fusion over encrypted vectors"
```

### Task 6: Settings section

**Files:**
- Modify: `profile-log/agentmesh_log/settings.py` (`validate`)
- Test: create `profile-log/tests/settings/test_semantic_settings.py` (the autouse `isolated_agentmesh_home` fixture in `profile-log/tests/conftest.py` already isolates `AGENTMESH_HOME`, so broker tests never read the owner's real `semantic` block)

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from agentmesh_log import settings

def test_semantic_section_valid():
    settings.validate({'semantic': {'mode': 'hybrid', 'model': 'bge-m3:latest',
        'embed_labels': ['general', 'internal'], 'min_score': 0.45}})

@pytest.mark.parametrize('bad', [{'mode': 'semantic'}, {'min_score': 1.5}, {'embed_labels': []},
    {'embed_labels': ['public']}, {'model': 'http://x'}, {'extra': 1}])
def test_semantic_section_invalid(bad):
    with pytest.raises(ValueError, match='invalid_settings'): settings.validate({'semantic': bad})
```

- [ ] **Step 2: Run them.** Expected: FAIL, because `semantic` is not an allowed top-level field.
  Also add these local-only cases: `settings.validate({'semantic': {'endpoint': 'https://embed.krisb8.com', 'credentials': 'keychain:org.agentmesh.embed'}}, local=True)` passes. The same dict without `local=True` raises (the endpoint is device-local and never synced). `endpoint` without `credentials`, `http://` remote endpoints and bad references all raise.

- [ ] **Step 3: Implement.** Add `'semantic'` to the top-level `fields(...)` list and add:

```python
    if 'semantic' in value:
        s = value['semantic']; fields(s, ['mode', 'model', 'embed_labels', 'min_score'] + (['endpoint', 'credentials'] if local else []))
        if 'endpoint' in s or 'credentials' in s:
            check('endpoint' in s and 'credentials' in s)
            check(type(s['endpoint']) is str and bool(re.fullmatch(r'https://[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+', s['endpoint'])))
            check(type(s['credentials']) is str and bool(re.fullmatch(r'(?:keychain|wincred):[A-Za-z0-9.-]+', s['credentials'])))
        if 'mode' in s: check(s['mode'] in ('keyword', 'hybrid'))
        if 'model' in s: check(type(s['model']) is str and bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}', s['model'])) and '//' not in s['model'])
        if 'embed_labels' in s:
            v = s['embed_labels']; check(type(v) is list and bool(v) and len(set(v)) == len(v) and set(v) <= {'general', 'internal', 'secret'})
        if 'min_score' in s: check(type(s['min_score']) in (int, float) and 0 <= s['min_score'] <= 1)
```

- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(settings): semantic recall section"`

### Task 7: Wire into the broker service

**Files:**
- Modify: `profile-log/agentmesh_log/broker/service.py`
- Test: `profile-log/tests/broker/test_service.py`

- [ ] **Step 1: Write the failing tests**

```python
from agentmesh_log.broker.semantic import Semantic
from .support import FakeEmbedding

def hybrid(b, home, **kw):
    b.semantic = Semantic(home.root/'index', b.state.index_master_key, provider=FakeEmbedding(**kw), min_score=0.5)

def test_hybrid_recall_through_service(tmp_path):
    home,state,store,agents = setup_home(tmp_path); b = Broker(home); a = Session(agents[0].certificate())
    remember(b,a,agents[0]); remember(b,a,agents[0],body='ย้ายฐานข้อมูลไปเครื่องใหม่')
    hybrid(b, home); b.backfill_once()
    out = b.request(a, dict(op='recall', query='database'))
    assert len(out['results']) == 2 and out['query_plan']['retrieval']['effective'] == 'hybrid'
    assert b.request(a, dict(op='recall', query='database', mode='keyword'))['results'][0]['content'] == 'migrate database'
    assert 'retrieval' not in b.request(a, dict(op='recall', query='database', mode='keyword'))['query_plan']
    assert b.request(a, dict(op='recall', query='x', mode='vector')) == {'error': 'invalid_request'}

def test_hybrid_without_semantic_reports_not_configured(tmp_path):
    home,state,store,agents = setup_home(tmp_path); b = Broker(home); a = Session(agents[0].certificate())
    remember(b,a,agents[0])
    out = b.request(a, dict(op='recall', query='database', mode='hybrid'))
    assert out['query_plan']['retrieval'] == dict(requested='hybrid', effective='keyword', degraded='not_configured')

def test_cache_saved_encrypted_on_close(tmp_path):
    home,state,store,agents = setup_home(tmp_path); b = Broker(home); a = Session(agents[0].certificate())
    remember(b,a,agents[0]); hybrid(b, home); b.backfill_once(); b.close()
    [path] = (home.root/'index').glob('vectors-*.snap'); assert b'migrate' not in path.read_bytes()
```

- [ ] **Step 2: Run them.** Expected: FAIL (`mode` is not in the schema, and `backfill_once` is missing).
- [ ] **Step 3: Implement** in `service.py`:
  - `SCHEMAS['recall']`: add `mode` → `set('query project task limit profiles scopes compat mode'.split())`.
  - In `Broker.__init__`, after `self.imports=Imports(self)`:

```python
        self.semantic=None; self._semantic_wake=threading.Event()
        try:
            from .. import settings
            from .semantic import Semantic
            self.semantic=Semantic.from_settings(home.root/'index',self.state.index_master_key,settings.load().get('semantic'))
        except Exception: self.semantic=None
```

  - In `request()`, before `with self.lock:`, warm the query vector outside the lock. A cold model load (~2.4 s) or a slow Ollama then never blocks other agents:

```python
        if self.semantic is not None and type(request) is dict and request.get('op')=='recall' \
                and request.get('mode','hybrid')!='keyword' and type(request.get('query')) is str and 1<=len(request['query'])<=2048:
            self.semantic.prefetch(request['query'])
```

  - In `dispatch` `op=='recall'`: validate `mode=req.get('mode')`; `if mode is not None and mode not in ('keyword','hybrid'): raise ValueError('invalid_request')`. Then:

```python
            semantic=None if mode=='keyword' else self.semantic
            if mode=='hybrid' and semantic is None: semantic=_NOT_CONFIGURED
            result=self.index(grant).recall(analyze_query(query,project=req.get('project'),task=req.get('task')),
                limit,{scope_names[s] for s in scopes},{scope_names[p] for p in profiles},compat=req.get('compat',False),semantic=semantic)
            audit['mode']=result['query_plan'].get('retrieval',{}).get('effective','keyword')
```

    with a module-level stub:

```python
class _NotConfigured:
    def fuse(self, plan, keyword, authorized, limit):
        return keyword, dict(requested='hybrid', effective='keyword', degraded='not_configured')
_NOT_CONFIGURED=_NotConfigured()
```

    `Audit.append` (`broker/audit.py:30`) takes fixed keywords, so add `mode=None` to its signature, write `mode=mode or 'keyword'` into the row, and extend `test_audit.py` with one assertion that a hybrid recall logs `mode: 'hybrid'`. The audit dict is passed through as `**audit`. Check the call site in `_request` and make sure the new key reaches it.
  - Backfill: collect under the lock, embed outside it, store under it:

```python
    def backfill_once(self):
        semantic=self.semantic
        if semantic is None or not semantic.ensure(): return 0
        with self.lock:
            rows=[r for index in self.indexes.values() for r in index.rows()]
            texts=semantic.pending(rows)
        try: pairs=semantic.embed_texts(texts)
        except (RuntimeError,ValueError): return 0
        with self.lock:
            for text,vector in pairs: semantic.store.put(text,vector)
            semantic.save()
        return len(pairs)
```

  - In `ingest()`, after the index loop: `self._semantic_wake.set()`.
  - In `flush_indexes()`, at the end: `if self.semantic is not None: self.semantic.save({r['content'] for i in self.indexes.values() for r in i.rows()})`. Pruning happens only here, under the lock, at flush/close.
  - In `serve()`, start a daemon thread next to `sync_loop`:

```python
            def semantic_loop():
                while not self.stopped.is_set():
                    while self.backfill_once()>0 and not self.stopped.is_set(): pass
                    self._semantic_wake.wait(300); self._semantic_wake.clear()
            if self.semantic is not None: threading.Thread(target=semantic_loop,daemon=True).start()
```

- [ ] **Step 4: Run** `python -m pytest -q profile-log/tests`. Expected: all pass.
- [ ] **Step 5: Commit** `git commit -am "feat(broker): opt-in hybrid recall mode"`

### Task 8: CLI flag and memory-sync compatibility

**Files:**
- Modify: `profile-log/agentmesh_log/cli.py:54-85,183`
- Test: `profile-log/tests/broker/test_cli.py`, `memory-sync/test_profile_log_bridge.py`

- [ ] **Step 1: Add the CLI flags.** `recall.add_argument('--mode',choices=('keyword','hybrid'))`, and in the request dict add `**({'mode':args.mode} if args.mode else {})`. Without the flag, the broker uses the settings default. Add `--kind` with `choices=('s3','access')` and default `'s3'` to the `r2-credentials` subcommands, passed through as `credentials_command(args.action,args.ref,kind=args.kind)`; extend `test_cli.py` for both.
- [ ] **Step 2: Add the memory-sync contract test.** The bridge rejects any top-level key outside `{'query','query_plan','candidate_counts','results','evidence'}`, and it requires `candidate_counts` to hold ints only. Add this test to `memory-sync/test_profile_log_bridge.py` (follow the file's existing broker stub helpers):

```python
def test_hybrid_shape_accepted_and_order_kept(tmp_path, monkeypatch):
    answer = dict(query='database', query_plan={'retrieval': {'requested':'hybrid','effective':'hybrid','coverage':{'ranked':2,'missing':0}}},
        candidate_counts=dict(durable=1,summaries=0,events=0), evidence=[],
        results=[synthetic_row('kw'), {**synthetic_row('th'), 'ranking': {'keyword_rank':None,'semantic_rank':1,'cosine':0.8,'rrf':0.016}}])
    # route _profile_recall's subprocess to return json.dumps(answer); assert broker_recall keeps ids ['kw','th']
```

  Build `synthetic_row` from the required-field set at `memory-sync/profile_log_bridge.py:108`. Run `python -m pytest -q memory-sync/test_profile_log_bridge.py`. Expected: PASS without changing the bridge, because extra row keys and nested `query_plan` keys are already allowed. If it fails, change the bridge minimally and note it in the report.
- [ ] **Step 3: Commit** `git commit -am "feat(cli): recall --mode; pin memory-sync hybrid contract"`

### Task 9: Owner-run eval and performance gate

**Files:**
- Create: `profile-log/tests/broker/eval_semantic.py` (not collected by pytest: no `test_` prefix, same as `bench_broker.py`)

- [ ] **Step 1: Write the script.** It reads queries from a local file given as argv (never committed). For each query it runs the CLI recall in both modes N=5 times through the configured `cli_argv`/agent key, then prints, per query: keyword top-5 ids, hybrid top-5 ids, new ids with their cosine, p50/p95 latency per mode, and the coverage `missing`. The output contains ids and numbers only. It never prints content, so the result can be pasted into a report.
- [ ] **Step 2: Owner runs it on the Air (host) and the Mac (tunnel client)** with 20 real queries (Thai and English, including paraphrases that share no keyword with the target memory). Gates:
  - warm hybrid p95 ≤ keyword p95 + 250 ms;
  - first query after a cold model returns within the bridge `timeout_seconds`, as keyword with `degraded`, or as hybrid;
  - owner judges hybrid top-5 to be no worse on at least 17/20 queries and better on at least 3 paraphrase queries;
  - calibrate `min_score` from the printed cosine distribution (bge-m3 unrelated pairs usually sit around 0.3–0.4). Record the chosen value.
- [ ] **Step 3: Commit the script** `git add profile-log/tests/broker/eval_semantic.py && git commit -m "test(broker): owner-run semantic eval"`

### Task 10: Docs

**Files:**
- Modify: `profile-log/README.md` (new "Phase 8: hybrid recall" section), `docs/use/recall.md`, `docs/privacy.md`

- [ ] **Step 1: Document** the following: the opt-in `semantic` settings block with an example, the loopback-only Ollama requirement, the secret-label default, the encrypted cache with no sync and no backup, the degrade behavior and the `query_plan.retrieval` fields, the ABA/OLLAMA_DEBUG caveat, and the measured costs table from this plan.
- [ ] **Step 2: Run** `bash docs/scripts/check-privacy.sh`. Expected: exit 0, with no private paths or hostnames.
- [ ] **Step 3: Commit** `git commit -am "docs: hybrid recall and vector cache privacy"`

### Task 11: Rollout (owner-run, after merge)

- [ ] Air (host): install Ollama and `ollama pull bge-m3`, keeping it listening on 127.0.0.1. Deploy the app, add `"semantic": {"mode": "hybrid"}` to `~/.agentmesh/settings.json` (loopback, no `endpoint`), restart the broker and wait for backfill (watch `coverage.missing` reach 0 via the eval script). Run the Task 9 gates. Decide on the Air's sleep policy, for example `caffeinate`/`pmset` while on power, or accept degraded keyword recall while it sleeps.
- [ ] Central endpoint (owner-run on the Air, dashboard-managed like the existing tunnels):
  1. Create tunnel `agentmesh-embed-air` with a dedicated connector on the Air: launchd `org.agentmesh.embed-tunnel` → a wrapper script that reads the tunnel token from Keychain item `org.agentmesh.embed.tunnel`, following the `insight-tunnel.sh` pattern. Store the token with `-w "$(pbpaste)"`, because the prompt truncates at 128 chars. Metrics go on 127.0.0.1:20243.
  2. Published application `embed.krisb8.com`, path `^/api/(embed|tags)$` → `http://127.0.0.1:11434`, with a catch-all of `http_status:404`. Do not attach the hostname as a Worker custom domain, because that shadows the tunnel route.
  3. Access application for `embed.krisb8.com`, policy Service Auth → service token `agentmesh-embed`, and nothing else.
  4. From another device: `curl -s https://embed.krisb8.com/api/tags` without the token must return the Access 403 (or the login redirect). With the token it must return the model list. `/api/generate` with the token must return 404.
  5. Ollama on the Air must keep `bge-m3:latest` frozen (no `ollama pull` while in use). Set `OLLAMA_KEEP_ALIVE=30m` for the Ollama app.
- [ ] Mac and Windows (clients): store the service token (Windows: `python -m agentmesh_log.cli r2-credentials store wincred:org.agentmesh.embed --kind access`; Mac: `security add-generic-password -s org.agentmesh.embed -a client-id -w …` and the same for `client-secret`). Then add `"semantic": {"endpoint": "https://embed.krisb8.com", "credentials": "keychain:org.agentmesh.embed"}` to that device's `settings.local.json`; Windows uses `wincred:`. Restart the broker and run the Task 9 gates there. Expect the first-run backfill over the tunnel to take longer. Record the measured remote query-embed latency.
- [ ] If the Air is asleep or offline, the other devices answer keyword-only with `degraded`, which is expected. If that happens too often, the fix is a different always-on host. No code changes are needed.
- [ ] Rollback: set `"mode": "keyword"` or remove the block and restart. Optionally delete `index/vectors-*.snap`. Nothing else depends on the cache.

---

## Self-review notes

- Spec coverage: bge-m3 via local Ollama (Tasks 2, 5, 7), hybrid RRF (Task 5), encrypted vectors (Tasks 3, 7), performance (measurements + Task 9 gates), production path through memory-sync `--compat` (Tasks 4, 8).
- Keyword-mode invariance: Task 4 characterization test + unchanged `query_plan` keys when mode is keyword.
- Known limits accepted for v1: pure-Python cosine is linear (about 108 ms at 5k rows). Above roughly 10k rows, add an optional NumPy path or the existing `rank_hnsw`. Query text goes to local Ollama. Vectors of secret content are not produced unless the owner adds `secret` to `embed_labels`.
