import pytest
from agentmesh_log.broker.index import AgentIndex
from agentmesh_log.broker.query import analyze_query
from agentmesh_log.broker.embedder import InputRejected
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

def test_provider_down(tmp_path):
    a = corpus(); s = make(tmp_path, fail=True)
    out = a.recall(analyze_query('migrate database'), 12, {'work/a'}, {'work'}, semantic=s)
    assert [r['id'] for r in out['results']] == ['kw']
    assert out['query_plan']['retrieval'] == dict(requested='hybrid', effective='keyword', degraded='provider_unavailable')

def test_missing_vectors(tmp_path):
    a = corpus(); s = make(tmp_path)  # no backfill
    out = a.recall(analyze_query('migrate database'), 12, {'work/a'}, {'work'}, semantic=s)
    assert [r['id'] for r in out['results']] == ['kw'] and out['query_plan']['retrieval']['coverage']['missing'] == 3

def test_authorization(tmp_path):
    a = corpus(); s = make(tmp_path); s.backfill_rows(a.rows())
    out = a.recall(analyze_query('ฐานข้อมูล'), 12, {'work/b'}, {'work'}, semantic=s)
    assert out['results'] == []

def test_save_load_cache(tmp_path):
    a = corpus(); s = make(tmp_path); s.backfill_rows(a.rows()); s.save()
    t = make(tmp_path); assert t.store.count() == 3
    assert not any(p.name.endswith('.tmp') for p in (tmp_path).iterdir())


def test_settings_local(tmp_path, monkeypatch):
    from agentmesh_log.broker import embedder
    calls = []
    def factory(**kwargs):
        calls.append(kwargs)
        return FakeEmbedding()
    monkeypatch.setattr(embedder, 'OllamaEmbedding', factory)
    s = Semantic.from_settings(tmp_path, b'k'*32, {'mode': 'hybrid'})
    assert calls == []  # Construction must not contact the provider.
    assert s.ensure()
    assert calls == [{'model': 'bge-m3:latest', 'timeout': 4}]


def test_settings_remote(tmp_path, monkeypatch):
    from agentmesh_log.broker import embedder
    from agentmesh_log.storage import r2creds
    calls = []
    def credentials(ref, *, kind):
        calls.append((ref, kind))
        return 'test-id', 'test-secret'
    def factory(**kwargs):
        calls.append(kwargs)
        return FakeEmbedding()
    monkeypatch.setattr(r2creds, 'load_credentials', credentials)
    monkeypatch.setattr(embedder, 'OllamaEmbedding', factory)
    s = Semantic.from_settings(tmp_path, b'k'*32, {
        'mode': 'hybrid', 'model': 'custom:latest',
        'endpoint': 'https://embed.example.com',
        'credentials': 'keychain:org.agentmesh.embed',
        'embed_labels': ['general'], 'min_score': 0.7,
    })
    assert calls == []
    assert s.ensure()
    assert calls == [('keychain:org.agentmesh.embed', 'access'), {
        'model': 'custom:latest', 'base_url': 'https://embed.example.com',
        'timeout': 4, 'headers': {
            'CF-Access-Client-Id': 'test-id',
            'CF-Access-Client-Secret': 'test-secret',
        },
    }]
    assert s.labels == frozenset({'general'}) and s.min_score == 0.7


def test_credentials_down(tmp_path, monkeypatch):
    from agentmesh_log.broker import semantic
    from agentmesh_log.storage import StorageError, r2creds
    calls = []
    def credentials(ref, *, kind):
        calls.append((ref, kind))
        raise StorageError('embedding credentials refused')
    monkeypatch.setattr(r2creds, 'load_credentials', credentials)
    monkeypatch.setattr(semantic.time, 'monotonic', lambda: 100)
    s = Semantic.from_settings(tmp_path, b'k'*32, {
        'mode': 'hybrid', 'endpoint': 'https://embed.example.com',
        'credentials': 'keychain:org.agentmesh.embed',
    })
    for _ in range(2):
        out = corpus().recall(analyze_query('migrate database'), 12,
                              {'work/a'}, {'work'}, semantic=s)
        assert [r['id'] for r in out['results']] == ['kw']
        assert out['query_plan']['retrieval'] == dict(
            requested='hybrid', effective='keyword', degraded='provider_unavailable')
    assert calls == [('keychain:org.agentmesh.embed', 'access')]


def test_keyword_settings(tmp_path):
    for section in (None, {}, {'mode': 'keyword'}):
        assert Semantic.from_settings(tmp_path, b'k'*32, section) is None


def test_revision_clears_queries(tmp_path):
    s = make(tmp_path)
    s.query_vector('database')
    assert len(s._queries) == 1
    provider = FakeEmbedding(revision='e'*64)
    s._attach(provider)
    assert not s._queries
    s.query_vector('database')
    assert provider.calls == [['database']]


def test_ensure_concurrent(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import time

    start = threading.Barrier(2)
    calls = []
    def factory():
        calls.append(1)
        time.sleep(0.2)
        return FakeEmbedding()
    s = Semantic(tmp_path, b'k'*32, factory=factory)
    def connect():
        start.wait(timeout=5)
        return s.ensure()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(connect) for _ in range(2)]
        results = [future.result(timeout=5) for future in futures]
    assert calls == [1]
    assert results == [True, True]


def test_rrf_values(tmp_path):
    s = make(tmp_path)
    kw = {**row('x'), 'score': 10}
    fused, _ = s.fuse(analyze_query('database'), [kw], lambda: [], 1)
    assert fused[0]['ranking']['rrf'] == round(1/61, 6)
    s.backfill_rows([kw])
    fused, _ = s.fuse(analyze_query('database'), [kw], lambda: [kw], 1)
    assert fused[0]['ranking']['rrf'] == round(2/61, 6)


def test_rrf_order(tmp_path):
    s = make(tmp_path)
    high = {**row('high'), 'content': 'service', 'score': 100}
    low = {**row('low'), 'score': 1}
    s.backfill_rows([high, low])
    fused, _ = s.fuse(analyze_query('database'), [high, low],
                      lambda: [high, low], 2)
    assert [r['id'] for r in fused] == ['low', 'high']
    assert fused[0]['ranking']['semantic_rank'] == 1
    assert fused[1]['ranking']['semantic_rank'] is None


def test_keyword_row_wins(tmp_path):
    s = make(tmp_path)
    kw = {**row('x'), 'score': 10, 'src': 'kw'}
    sem = {**kw, 'src': 'sem'}
    s.backfill_rows([sem])
    fused, _ = s.fuse(analyze_query('database'), [kw], lambda: [sem], 1)
    assert len(fused) == 1
    assert fused[0]['src'] == 'kw'
    assert fused[0]['ranking']['semantic_rank'] == 1


def test_semantic_pool_cap(tmp_path):
    s = make(tmp_path)
    rows = [{**row(f'r{i:02}'), 'score': 1} for i in range(60)]
    s.backfill_rows(rows)
    fused, info = s.fuse(analyze_query('database'), [], lambda: rows, 1)
    assert sum(r['ranking']['semantic_rank'] is not None for r in fused) == 50
    assert info['coverage']['ranked'] == 60


def test_pending_cache(tmp_path):
    s = make(tmp_path)
    rows = corpus().rows()
    s.backfill_rows(rows)
    assert s.pending(rows) == []


def test_pending_budget(tmp_path):
    s = make(tmp_path)
    rows = [{**row(str(i)), 'content': f'database {i}'} for i in range(300)]
    assert len(s.pending(rows)) == 256
    assert len(s.pending(rows, budget=5)) == 5


def test_batch_items(tmp_path):
    s = make(tmp_path)
    texts = [f'database {i}' for i in range(70)]
    pairs = s.embed_texts(texts)
    assert [len(call) for call in s.provider.calls] == [32, 32, 6]
    assert [text for text, _ in pairs] == texts


def test_batch_chars(tmp_path):
    s = make(tmp_path)
    texts = [str(i)*30000 for i in range(5)]
    pairs = s.embed_texts(texts)
    assert [len(call) for call in s.provider.calls] == [4, 1]
    assert [text for text, _ in pairs] == texts


class RejectsLong(FakeEmbedding):
    """Refuses any batch containing a text over 100 characters, like Ollama past its context."""
    def embed(self, texts):
        if any(len(t) > 100 for t in texts):
            self.calls.append(list(texts)); raise InputRejected('Ollama rejected the input (HTTP 400)')
        return super().embed(texts)


def test_rejected_text_skipped(tmp_path):
    s = Semantic(tmp_path, b'k'*32, mode='hybrid', provider=RejectsLong(), min_score=0.5)
    a = AgentIndex('a', b'a'*32)
    a.replace([row('kw'), {**row('long'), 'content': 'database '*20}, {**row('db'), 'content': 'database rollout'}])
    assert s.backfill_rows(a.rows()) == 2
    assert s.rejected == {'database '*20} and s.pending(a.rows()) == []
    out = a.recall(analyze_query('database'), 12, {'work/a'}, {'work'}, semantic=s)
    assert out['query_plan']['retrieval']['coverage'] == dict(ranked=2, missing=0, rejected=1)


def test_provider_down_not_rejected(tmp_path):
    s = make(tmp_path, fail=True)
    with pytest.raises(RuntimeError): s.embed_texts(['database'])
    assert s.rejected == set()


def test_stale_cache_removed(tmp_path):
    stale = tmp_path/'vectors-0000000000000000.snap'
    stale.write_bytes(b'stale')
    s = make(tmp_path)
    assert not stale.exists()
    s.backfill_rows([row()])
    s.save()
    current = s.path
    assert current.is_file()
    fresh = make(tmp_path)
    assert current.is_file() and fresh.store.count() == 1


def test_query_lru_cap(tmp_path):
    s = make(tmp_path)
    for i in range(300):
        s.query_vector(f'database {i}')
    assert len(s._queries) == 256
    assert len(s.provider.calls) == 300
    s.query_vector('database 0')
    assert len(s.provider.calls) == 301
    assert s.provider.calls[-1] == ['database 0']


def test_save_retains_live(tmp_path):
    s = make(tmp_path)
    s.backfill_rows([{**row('a'), 'content': 'a'}, {**row('b'), 'content': 'b'}])
    assert s.store.count() == 2
    s.save(live_texts={'a'})
    fresh = make(tmp_path)
    assert fresh.store.count() == 1
    assert fresh.pending([{**row('a'), 'content': 'a'}]) == []
    assert fresh.pending([{**row('b'), 'content': 'b'}]) == ['b']


def test_fuse_cache_only(tmp_path):
    s = make(tmp_path); s.fetch_in_fuse = False
    keyword = [{**row('kw'), 'score': 1}]
    fused, info = s.fuse(analyze_query('database'), keyword, lambda: [], 12)
    assert fused is keyword
    assert info == dict(requested='hybrid', effective='keyword', degraded='provider_unavailable')
    assert s.provider.calls == []
    s.prefetch('database')
    _, info = s.fuse(analyze_query('database'), keyword, lambda: [], 12)
    assert info['effective'] == 'hybrid'
    assert s.provider.calls == [['database']]


@pytest.mark.parametrize('error', [RuntimeError, ValueError], ids=['runtime','value'])
def test_prefetch_backoff(tmp_path, monkeypatch, error):
    from agentmesh_log.broker import semantic
    monkeypatch.setattr(semantic.time, 'monotonic', lambda: 100)
    calls = []
    provider = FakeEmbedding()
    def embed(texts):
        calls.append(('embed', texts))
        raise error('offline')
    provider.embed = embed
    def factory():
        calls.append(('factory',))
        return provider
    s = Semantic(tmp_path, b'k'*32, factory=factory)
    s.prefetch('database'); s.prefetch('database')
    assert calls == [('factory',), ('embed', ['database'])]
    assert s.provider is None
