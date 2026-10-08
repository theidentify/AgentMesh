"""Permission-filtered, revision-aware retrieval; projections are not authority."""
from contextlib import closing
import json
import os
import re
import sqlite3
import tempfile

from .core import (AccessDenied, Conflict, KnowledgeError, Unavailable, bounded_text,
                   canonical, checked_path, profile_id, digest)
from .vectors import validate_vectors, rank_exact, rank_hnsw
import hashlib


MODES = {'keyword', 'semantic', 'hybrid'}
ENGINES = {'exact', 'hnsw'}


def defaults(api):
    if api.principal not in api.stores:
        return {'mode': 'keyword', 'vector_engine': 'exact'}
    path = checked_path(api.stores[api.principal].root / 'state' / 'retrieval.json')
    if not path.exists():
        return {'mode': 'keyword', 'vector_engine': 'exact'}
    if path.stat().st_size > 1024:
        raise KnowledgeError('invalid retrieval configuration size')
    value = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(value, dict) or set(value) != {'mode', 'vector_engine'} or
            value['mode'] not in MODES or value['vector_engine'] not in ENGINES):
        raise KnowledgeError('invalid retrieval configuration')
    return value


def configure(api, *, mode, vector_engine='exact'):
    if mode not in MODES or vector_engine not in ENGINES:
        raise KnowledgeError('invalid retrieval configuration')
    value = {'mode': mode, 'vector_engine': vector_engine}
    path = checked_path(api._store(api.principal).root / 'state' / 'retrieval.json')
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     prefix='retrieval-', suffix='.tmp', delete=False) as out:
        temporary = out.name
        out.write(canonical(value))
        out.flush()
        os.fsync(out.fileno())
    try:
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return value


MAX_CANDIDATES = 2000


def candidates(api, *, project=None, owners=None):
    selected = list(api.stores) if owners is None else owners
    if not isinstance(selected, list) or len(selected) > 64 or len(set(selected)) != len(selected):
        raise KnowledgeError('invalid requested profiles')
    results, partial = {}, []
    for owner in selected:
        if owner not in api.stores:
            partial.append(owner)
            continue
        for record in api.stores[owner].records():
            if (api.principal not in record['policy']['read'] or record['status'] != 'active' or
                    (project is not None and record['project'] != project)):
                continue
            try:
                current = api._record(owner, record['id'])
            except (AccessDenied, Conflict):
                continue
            token = owner + '|' + record['id']
            results[token] = current
            if len(results) > MAX_CANDIDATES:
                raise KnowledgeError('prototype candidate limit exceeded; use narrower scope')
    return results, partial


def rank_keyword(records, query, limit):
    terms = re.findall(r'\w+', query, flags=re.UNICODE)
    if not terms or not records:
        return []
    expression = ' OR '.join('"' + term.replace('"', '""') + '"' for term in terms[:64])
    with closing(sqlite3.connect(':memory:')) as conn:
        conn.execute("CREATE VIRTUAL TABLE docs USING fts5(token UNINDEXED, content, tokenize='unicode61')")
        conn.executemany('INSERT INTO docs(token,content) VALUES(?,?)',
                         [(token, record['content']) for token, record in records.items()])
        return [(row[0], -row[1]) for row in conn.execute(
            'SELECT token,bm25(docs) AS score FROM docs WHERE docs MATCH ? ORDER BY score,token LIMIT ?',
            (expression, limit))]


def checked_space(provider, allow_unknown_dimension=False):
    try:
        value = dict(provider.space)
    except (AttributeError, TypeError, ValueError) as exc:
        raise KnowledgeError('invalid embedding provider identity') from exc
    if set(value) != {'provider', 'model', 'revision', 'dimension', 'metric'} or value['metric'] != 'cosine':
        raise KnowledgeError('invalid embedding space')
    for key in ('provider', 'model', 'revision'):
        bounded_text(value[key], 'embedding ' + key, 256)
    dimension = value['dimension']
    if dimension is None and allow_unknown_dimension:
        return value
    if type(dimension) is not int or not 1 <= dimension <= 8192:
        raise KnowledgeError('invalid embedding dimension')
    return value


def same_space(first, second):
    return all(first[key] == second[key] for key in ('provider', 'model', 'revision', 'metric')) and (
        first['dimension'] is None or first['dimension'] == second['dimension'])


def content_hash(record):
    return hashlib.sha256(record['content'].encode('utf-8')).hexdigest()


def projection_path(store, principal):
    profile_id(principal)
    name = 'vectors.sqlite3' if principal == store.profile_id else 'vectors-' + principal + '.sqlite3'
    return checked_path(store.root / 'indexes' / name)


def approved_batch(api, batch):
    for record in batch:
        try:
            current = api._record(record['owner'], record['id'])
        except (AccessDenied, Conflict) as exc:
            raise Unavailable('knowledge authorization changed during indexing') from exc
        if (current['revision'] != record['revision'] or current['status'] != 'active' or
                'local' not in current['policy']['embed']):
            raise Unavailable('knowledge revision or embedding approval changed during indexing')


def rebuild(api, provider, *, owner=None):
    owner = api.principal if owner is None else profile_id(owner)
    store = api._store(owner)
    records, _ = candidates(api, owners=[owner])
    eligible = [records[key] for key in sorted(records) if 'local' in records[key]['policy']['embed']]
    if not eligible:
        raise Unavailable('no active knowledge is approved for local embedding')
    initial = checked_space(provider, allow_unknown_dimension=True)
    vectors = []
    for start in range(0, len(eligible), 16):
        batch = eligible[start:start + 16]
        # A SQLite write reservation is the disclosure fence for all canonical
        # writers, including other API instances/processes and mirror imports.
        # Revocation can commit between batches, never in a check/send window.
        with store.connection() as disclosure:
            disclosure.execute('BEGIN IMMEDIATE')
            approved_batch(api, batch)
            rows = validate_vectors(provider.embed([record['content'] for record in batch]), expected_count=len(batch))
            space = checked_space(provider)
            if not same_space(initial, space):
                raise Unavailable('embedding identity changed during indexing')
            vectors.extend(rows)
    space = checked_space(provider)
    vectors = validate_vectors(vectors, expected_count=len(eligible), expected_dimension=space['dimension'])
    path = projection_path(store, api.principal)
    with store.connection() as disclosure:
        disclosure.execute('BEGIN IMMEDIATE')
        approved_batch(api, eligible)
        with closing(sqlite3.connect(path, timeout=15)) as conn, conn:
            conn.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            conn.execute('''CREATE TABLE IF NOT EXISTS vectors (
                id TEXT PRIMARY KEY, revision TEXT NOT NULL, content_hash TEXT NOT NULL, vector TEXT NOT NULL)''')
            conn.execute('DELETE FROM vectors')
            conn.execute('DELETE FROM metadata')
            conn.execute('INSERT INTO metadata VALUES(?,?)', ('space', canonical(space)))
            conn.executemany('INSERT INTO vectors VALUES(?,?,?,?)', [
                (record['id'], record['revision'], content_hash(record), canonical(vector))
                for record, vector in zip(eligible, vectors)])
    return {'indexed': len(eligible), 'excluded': len(records) - len(eligible), 'space': space,
            'index_version': digest({'space': space, 'revisions': sorted((r['id'], r['revision']) for r in eligible)})}


def rank_semantic(api, records, query, provider, engine, limit):
    if provider is None:
        raise Unavailable('semantic retrieval requires an explicitly selected embedding provider')
    initial = checked_space(provider, allow_unknown_dimension=True)
    if not records:
        return [], {'ready': True, 'version': digest({'space': initial, 'revisions': []}),
                    'space': initial, 'coverage': 0}
    rows, space = {}, None
    try:
        for owner in sorted({record['owner'] for record in records.values()}):
            store = api._store(owner)
            path = projection_path(store, api.principal)
            if not path.exists():
                path = projection_path(store, owner)
            if not path.exists():
                raise Unavailable('current embedding index unavailable; explicitly rebuild')
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=15)) as conn:
                item = conn.execute("SELECT value FROM metadata WHERE key='space'").fetchone()
                if item is None:
                    raise Unavailable('embedding space metadata missing')
                current_space = json.loads(item[0])
                if not same_space(initial, current_space) or (space is not None and space != current_space):
                    raise Unavailable('embedding space mismatch; explicitly rebuild')
                space = current_space
                for token, record in records.items():
                    if record['owner'] != owner:
                        continue
                    row = conn.execute('SELECT revision,content_hash,vector FROM vectors WHERE id=?',
                                       (record['id'],)).fetchone()
                    if row is None or row[0] != record['revision'] or row[1] != content_hash(record):
                        raise Unavailable('embedding coverage is missing or stale; explicitly rebuild')
                    rows[token] = json.loads(row[2])
    except (sqlite3.Error, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, KnowledgeError):
            raise
        raise Unavailable('invalid derived embedding index; explicitly rebuild') from exc
    vectors = validate_vectors([rows[token] for token in sorted(rows)], expected_dimension=space['dimension'])
    q = validate_vectors(provider.embed([query]), expected_count=1, expected_dimension=space['dimension'])[0]
    if checked_space(provider) != space:
        raise Unavailable('query embedding space mismatch')
    ids = sorted(rows)
    ranked = rank_exact(ids, vectors, q, limit) if engine == 'exact' else rank_hnsw(ids, vectors, q, limit)
    metadata = {'ready': True, 'space': space, 'coverage': len(records),
                'version': digest({'space': space, 'engine': engine,
                                   'revisions': sorted((token, r['revision']) for token, r in records.items())})}
    return ranked, metadata


def search(api, query, *, mode=None, vector_engine=None, project=None,
           owners=None, limit=12, context_chars=8192, provider=None, expand_relations=False):
    bounded_text(query, 'query', 2048)
    settings = defaults(api)
    mode = settings['mode'] if mode is None else mode
    vector_engine = settings['vector_engine'] if vector_engine is None else vector_engine
    if not isinstance(mode, str) or mode not in MODES or not isinstance(vector_engine, str) or vector_engine not in ENGINES:
        raise KnowledgeError('invalid retrieval mode or engine')
    if type(expand_relations) is not bool:
        raise KnowledgeError('invalid relation expansion option')
    if project is not None:
        bounded_text(project, 'project', 128)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise KnowledgeError('invalid result limit')
    if not isinstance(context_chars, int) or isinstance(context_chars, bool) or not 256 <= context_chars <= 131072:
        raise KnowledgeError('invalid context budget')
    records, partial = candidates(api, project=project, owners=owners)
    budget = min(MAX_CANDIDATES, max(limit * 4, 100))
    index = {'ready': True, 'version': None, 'coverage': len(records)}
    if mode == 'keyword':
        ranked = rank_keyword(records, query, budget)
        keyword_ranks = {token: i for i, (token, _) in enumerate(ranked, 1)}
        semantic_ranks = {}
    else:
        ranked, index = rank_semantic(api, records, query, provider, vector_engine, budget)
        semantic_ranks = {token: i for i, (token, _) in enumerate(ranked, 1)}
        keyword_ranks = {}
        if mode == 'hybrid':
            lexical = rank_keyword(records, query, budget)
            keyword_ranks = {token: i for i, (token, _) in enumerate(lexical, 1)}
            tokens = set(keyword_ranks) | set(semantic_ranks)
            ranked = sorted([(token,
                (1 / (60 + keyword_ranks[token]) if token in keyword_ranks else 0) +
                (1 / (60 + semantic_ranks[token]) if token in semantic_ranks else 0))
                for token in tokens], key=lambda item: (-item[1], item[0]))
    results, used, truncated = [], 0, False
    for rank, (token, score) in enumerate(ranked, 1):
        record = records[token]
        try:
            result = api.get(record['owner'], record['id'])
        except (AccessDenied, Conflict):
            continue
        if result['revision'] != record['revision'] or result['status'] != 'active':
            continue
        result.update(rank=len(results) + 1, score=score,
                      ranking={'keyword_rank': keyword_ranks.get(token), 'semantic_rank': semantic_ranks.get(token)})
        if expand_relations:
            result['relations'] = api.related(record['owner'], record['id'], record['revision'])
        size = len(canonical(result))
        if used + size > context_chars:
            truncated = True
            continue
        results.append(result)
        used += size
        if len(results) == limit:
            truncated = len(ranked) > len(results)
            break
    return {'requested_mode': mode, 'effective_mode': mode,
            'vector_engine': None if mode == 'keyword' else vector_engine,
            'index': index, 'ranking': {'keyword': 'bm25', 'semantic': 'cosine', 'hybrid': 'rrf(k=60)'}[mode],
            'results': results, 'partial_profiles': partial,
            'context_chars_used': used, 'truncated': truncated}
