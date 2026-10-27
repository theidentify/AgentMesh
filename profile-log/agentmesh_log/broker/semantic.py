"""Opt-in hybrid recall: keyword ranks fused with cosine ranks (RRF k=60).

Candidates are the caller's AgentIndex rows (already grant/scope filtered).
Provider failure degrades a request to keyword and says so; it never guesses.
"""
from array import array
from collections import OrderedDict
from operator import mul
import threading
import time
from .embedder import InputRejected
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
        self.fetch_in_fuse = True
        self._factory = factory; self._next_attempt = 0.0
        self._queries = OrderedDict(); self._lock = threading.Lock()
        self._attach_lock = threading.Lock()
        self.provider = self.store = None
        # Texts the provider refused one by one; skipped by backfill so they cannot block it.
        self.rejected = set()
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
        with self._lock:
            self._queries.clear()
        self.provider = provider
        self.rejected = set()
        self.store = VectorStore(self.master_key, provider.space)
        self.path = self.index_dir/VectorStore.filename(provider.space)
        self.store.load(self.path)
        for stale in self.index_dir.glob('vectors-*.snap'):
            if stale != self.path: stale.unlink()

    def ensure(self):
        """Connect lazily; Ollama may start after the broker. Retry at most once a minute."""
        with self._attach_lock:
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
        except (RuntimeError, ValueError):
            self.provider = None if self._factory else self.provider

    def _eligible(self, row):
        return row.get('label') in self.labels and 0 < len(row['content']) <= MAX_TEXT

    def fuse(self, plan, keyword, authorized, limit):
        info = dict(requested=self.mode, effective=self.mode)
        if self.fetch_in_fuse:
            try: query = self.query_vector(plan.query)
            except (RuntimeError, ValueError):
                self.provider = None if self._factory else self.provider
                return keyword, {**info, 'effective': 'keyword', 'degraded': 'provider_unavailable'}
        else:
            # Broker fusion runs under its lock: only prefetch may contact the provider.
            with self._lock:
                query = self._queries.get(plan.query)
                if query is not None: self._queries.move_to_end(plan.query)
            if query is None:
                return keyword, {**info, 'effective': 'keyword', 'degraded': 'provider_unavailable'}
        scored = []; missing = 0; rejected = 0
        for row in authorized():
            if not self._eligible(row): continue
            vector = self.store.get(row['content'])
            if vector is None or len(vector) != len(query):
                if row['content'] in self.rejected: rejected += 1
                else: missing += 1
                continue
            cosine = sum(map(mul, query, vector))
            if cosine >= self.min_score: scored.append((cosine, row))
        scored.sort(key=lambda item: (-item[0], item[1]['id']))
        semantic = scored[:max(limit*4, 50)]
        info['coverage'] = dict(ranked=len(scored), missing=missing, rejected=rejected)
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
        return [t for t in dict.fromkeys(texts) if t not in self.rejected and not self.store.has(t)][:budget]

    def embed_texts(self, texts):
        """Provider calls only; caller stores results. Greedy char-bounded batches.

        A refused batch is retried one text at a time, so one oversized text is
        recorded in `rejected` instead of failing the whole backfill forever.
        """
        out = []; batch = []; size = 0
        for text in texts + [None]:
            if text is None or len(batch) == BATCH_ITEMS or size+len(text) > BATCH_CHARS:
                if batch: out.extend(self._embed_batch(batch))
                batch = []; size = 0
            if text is not None: batch.append(text); size += len(text)
        return out

    def _embed_batch(self, batch):
        try: return list(zip(batch, self.provider.embed(batch)))
        except InputRejected:
            if len(batch) == 1: self.rejected.add(batch[0]); return []
        out = []
        for text in batch:
            try: out.extend(zip([text], self.provider.embed([text])))
            except InputRejected: self.rejected.add(text)
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
