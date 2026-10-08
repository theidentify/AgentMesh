"""Small labeled fixture evaluation, not a production accuracy/scale guarantee."""
from pathlib import Path
import time

from .core import KnowledgeError, MemoryAPI, ProfileStore, checked_path


DOCUMENTS = {
    'provider': 'AgentMesh extraction provider must be switchable by configuration. Changing provider does not change profile identity. No automatic Claude fallback.',
    'folders': 'Each agent profile owns separate sources, knowledge, indexes, state and exports folders. No shared content database is required.',
    'transport': 'Synchronize immutable knowledge revisions, not a live SQLite database or WAL. Receiving peers acknowledge only after committing validated changes.',
    'retain': 'Read permission does not grant retention permission. Selective derived memory retains original knowledge ID and exact source revision.',
    'hnsw': 'HNSW is approximate vector navigation, not a graph of knowledge relationships. Use exact cosine search as an ANN recall baseline.',
    'task': 'TASK-42: preserve portable knowledge identifiers and exact revision provenance across profiles.',
    'orchard': 'The orchard inventory contains apples, pears and harvested fruit. This unrelated fixture is not about agent memory.',
}
QUERIES = [
    ('thai-provider', 'อยากเปลี่ยนผู้ให้บริการโมเดลได้ โดยไม่บังคับใช้ Claude อัตโนมัติ', 'provider'),
    ('thai-folders', 'แต่ละเอเจนต์แยกโฟลเดอร์ความจำของตัวเอง ไม่ต้องเก็บข้อมูลรวมกัน', 'folders'),
    ('thai-transport', 'ส่งต่อเฉพาะการแก้ไขความรู้ ห้ามซิงค์ฐานข้อมูล SQLite ที่กำลังเขียน', 'transport'),
    ('thai-retain', 'อ่านข้อมูลได้ ไม่ได้แปลว่าอนุญาตให้จำเป็นความรู้ถาวร ต้องเก็บที่มารุ่นเดิม', 'retain'),
    ('thai-hnsw', 'กราฟ HNSW เป็นทางลัดหาความใกล้เคียง ไม่ใช่ความสัมพันธ์เชิงความรู้', 'hnsw'),
    ('literal-task', 'TASK-42', 'task'),
    ('english-negation', 'Do not force a specific extraction vendor or automatically fall back to Claude.', 'provider'),
]
MODES = [('keyword', 'exact'), ('semantic', 'exact'), ('semantic', 'hnsw'), ('hybrid', 'exact'), ('hybrid', 'hnsw')]


class CountedProvider:
    def __init__(self, provider):
        self.provider = provider
        self.text_count = 0
        self.character_count = 0
        self.calls = 0

    @property
    def space(self):
        return self.provider.space

    def embed(self, texts):
        result = self.provider.embed(texts)
        self.calls += 1
        self.text_count += len(texts)
        self.character_count += sum(len(text) for text in texts)
        return result


def run(root, provider, k=3):
    root = checked_path(root)
    if root.exists() and any(root.iterdir()):
        raise KnowledgeError('evaluation requires a new empty output directory')
    stores = {name: ProfileStore(root / name, name) for name in ('alpha', 'beta')}
    alpha, beta = (MemoryAPI(stores, principal=name) for name in ('alpha', 'beta'))
    shared = {'read': ['alpha', 'beta'], 'retain': ['beta'], 'export': ['beta'], 'embed': ['local']}
    identifiers, records = {}, {}
    for label, text in DOCUMENTS.items():
        records[label] = alpha.remember(text, project='benchmark', policy=shared,
            source={'text': text, 'kind': 'source', 'locator': 'fixture:' + label}, quote=text)
        identifiers[label] = records[label]['id']
    corrected = alpha.revise(records['provider']['id'], records['provider']['revision'], content=DOCUMENTS['provider'] + ' Providers are not identity.',
        source={'text': DOCUMENTS['provider'] + ' Providers are not identity.', 'kind': 'source', 'locator': 'fixture:correction'},
        quote='Providers are not identity.')
    private = alpha.remember('PRIVATE-SENTINEL provider configuration.', project='benchmark',
                             policy={'read': ['alpha'], 'embed': ['local']})
    revoked = alpha.remember('REVOKED-SENTINEL provider configuration.', project='benchmark', policy=shared)
    alpha.revoke(revoked['id'], revoked['revision'])
    alpha.remember('No embedding permission.', project='elsewhere', policy={'read': ['alpha', 'beta']})
    counted = CountedProvider(provider)
    start = time.perf_counter()
    indexed = alpha.rebuild_index(counted)
    build_ms = (time.perf_counter() - start) * 1000
    by_id = {ident: label for label, ident in identifiers.items()}
    modes, all_ids, all_revisions = {}, [], []
    for mode, engine in MODES:
        name = mode if mode == 'keyword' else mode + '/' + engine
        rows = []
        for label, query, expected in QUERIES:
            start = time.perf_counter()
            response = beta.search(query, mode=mode, vector_engine=engine, provider=counted,
                                   project='benchmark', limit=k, context_chars=16384)
            elapsed_ms = (time.perf_counter() - start) * 1000
            ids = [result['id'] for result in response['results']]
            all_ids.extend(ids)
            all_revisions.extend((result['id'], result['revision']) for result in response['results'])
            rows.append({'label': label, 'query': query, 'expected': expected,
                         'returned': [by_id[ident] for ident in ids], 'latency_ms': elapsed_ms,
                         'context_chars': response['context_chars_used']})
        modes[name] = {'metrics': metrics(rows, k), 'mean_latency_ms': sum(r['latency_ms'] for r in rows) / len(rows),
                       'per_query': rows}
    exact = modes['semantic/exact']['per_query']
    ann = modes['semantic/hnsw']['per_query']
    neighbor_recalls = [len(set(a['returned']) & set(e['returned'])) / len(e['returned'])
                        for e, a in zip(exact, ann) if e['returned']]
    return {'format': 'agentmesh-retrieval-evaluation-v1', 'profiles': sorted(stores),
            'fixture_only': True, 'quality_claim': 'small labeled corpus only; no scale or downstream answer guarantee',
            'corpus_size': len(DOCUMENTS), 'query_count': len(QUERIES), 'space': counted.space,
            'index_build_ms': build_ms, 'indexed': indexed['indexed'], 'excluded_from_embedding': indexed['excluded'],
            'index_bytes': sum(path.stat().st_size for path in root.glob('*/indexes/*') if path.is_file()),
            'modes': modes, 'hnsw_neighbor_recall_at_k': sum(neighbor_recalls) / len(neighbor_recalls),
            'embedding_usage': {'text_count': counted.text_count, 'character_count': counted.character_count,
                                'requests': counted.calls, 'monetary_cost': None},
            'checks': {'private_excluded': private['id'] not in all_ids, 'revoked_excluded': revoked['id'] not in all_ids,
                       'superseded_excluded': (records['provider']['id'], records['provider']['revision']) not in all_revisions,
                       'correction_exposed_when_returned': all(revision == corrected['revision'] for ident, revision in all_revisions
                                                             if ident == corrected['id'])}}


def metrics(rows, k=3):
    if type(k) is not int or not 1 <= k <= 100 or not isinstance(rows, list) or not rows:
        raise KnowledgeError('nonempty labeled rows and a bounded k are required')
    hits, relevant, reciprocals = 0, 0, 0.0
    for row in rows:
        expected, returned = row['expected'], row['returned'][:k]
        if len(returned) != len(set(returned)):
            raise KnowledgeError('duplicate ranked identifiers')
        hits += bool(returned and returned[0] == expected)
        if expected in returned:
            relevant += 1
            reciprocals += 1 / (returned.index(expected) + 1)
    n = len(rows)
    return {'queries': n, 'k': k, 'hit_at_1': hits / n, 'recall_at_k': relevant / n,
            'precision_at_k': relevant / (n * k), 'mrr': reciprocals / n}
