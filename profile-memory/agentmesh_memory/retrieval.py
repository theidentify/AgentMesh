"""Permission-filtered, revision-aware retrieval; projections are not authority."""
from contextlib import closing
import json
import os
import re
import sqlite3
import tempfile

from .core import (AccessDenied, Conflict, KnowledgeError, Unavailable, bounded_text,
                   canonical, checked_path, profile_id)


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


def search(api, query, *, mode=None, vector_engine=None, project=None,
           owners=None, limit=12, context_chars=8192, provider=None):
    bounded_text(query, 'query', 2048)
    settings = defaults(api)
    mode = settings['mode'] if mode is None else mode
    vector_engine = settings['vector_engine'] if vector_engine is None else vector_engine
    if not isinstance(mode, str) or mode not in MODES or not isinstance(vector_engine, str) or vector_engine not in ENGINES:
        raise KnowledgeError('invalid retrieval mode or engine')
    if mode != 'keyword':
        raise Unavailable('semantic provider and current index are required')
    if project is not None:
        bounded_text(project, 'project', 128)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise KnowledgeError('invalid result limit')
    if not isinstance(context_chars, int) or isinstance(context_chars, bool) or not 256 <= context_chars <= 131072:
        raise KnowledgeError('invalid context budget')
    records, partial = candidates(api, project=project, owners=owners)
    ranked = rank_keyword(records, query, min(MAX_CANDIDATES, max(limit * 4, 100)))
    results, used, truncated = [], 0, False
    for rank, (token, score) in enumerate(ranked, 1):
        record = records[token]
        try:
            result = api.get(record['owner'], record['id'])
        except (AccessDenied, Conflict):
            continue
        if result['revision'] != record['revision'] or result['status'] != 'active':
            continue
        result.update(rank=len(results) + 1, score=score, ranking={'keyword_rank': rank, 'semantic_rank': None})
        size = len(canonical(result))
        if used + size > context_chars:
            truncated = True
            continue
        results.append(result)
        used += size
        if len(results) == limit:
            truncated = len(ranked) > len(results)
            break
    return {'requested_mode': mode, 'effective_mode': mode, 'vector_engine': None,
            'index': {'ready': True, 'version': None, 'coverage': len(records)},
            'ranking': 'bm25', 'results': results, 'partial_profiles': partial,
            'context_chars_used': used, 'truncated': truncated}
