"""Read-only, vector-free SQLite recall. Stored text is data, never instructions.

CLI: python recall_memory.py DB QUERY [--project PROJECT] [--limit 12]
Durable items and summaries are ranked before raw observation fallbacks.
No PostgreSQL imports, database initialization, or external services are used.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import sys

from sqlite_memory import TABLES

TASK_RE = re.compile(r'\b[A-Z][A-Z0-9]{1,15}-\d+\b', re.IGNORECASE)
WORD_RE = re.compile(r'[A-Za-z0-9_-]{2,}|[\u0E00-\u0E7F]{2,}')
STOP_WORDS = set('what when where which with from that this current about status memory project task ตอนนี้ อะไร ยังไง อย่างไร ของ และ หรือ ที่ ใน เป็น มี ไหม บ้าง ล่าสุด สถานะ งาน ใช้ เดิม แยก'.split())
INTENT_TERMS = {
    'status': ('status', 'current', 'latest', 'สถานะ', 'ล่าสุด', 'ถึงไหน'),
    'constraint': ('constraint', 'scope', 'exclude', 'ข้อจำกัด', 'ห้าม', 'ตัดออก'),
    'decision': ('decision', 'why', 'ทำไม', 'เลือก', 'ตัดสินใจ', 'เหตุผล'),
    'procedure': ('procedure', 'how', 'workflow', 'วิธี', 'ขั้นตอน', 'ควร', 'อย่างไร', 'ยังไง'),
    'preference': ('preference', 'prefer', 'ชอบ', 'ต้องการ'),
    'evidence': ('source', 'evidence', 'proof', 'อ้างอิง', 'หลักฐาน', 'มาจากไหน'),
}
THAI_SPLIT = re.compile('|'.join(re.escape(t) for t in sorted(
    STOP_WORDS | {'ทำไม', 'ควร', 'ทำ', 'ข้อจำกัด', 'สำคัญ', 'แต่'}, key=lambda t: (-len(t), t))))
KIND_WEIGHTS = {'constraint': 16, 'open_loop': 15, 'decision': 14,
                'procedure': 12, 'preference': 11, 'fact': 10, 'summary': 9,
                'user_request': 5, 'assistant_response': 4, 'verification': 4,
                'error': 3, 'tool_result': 1, 'tool_call': 0}
EVENT_KINDS = ('user_request', 'assistant_response', 'decision', 'verification', 'error', 'task_transition')


@dataclass(frozen=True)
class QueryPlan:
    query: str
    project: str | None
    task_refs: tuple[str, ...]
    intents: tuple[str, ...]
    terms: tuple[str, ...]


def analyze_query(query, *, project=None, task=None):
    normalized = ' '.join(query.split())
    terms = []
    for token in WORD_RE.findall(normalized.lower()):
        pieces = THAI_SPLIT.split(token) if re.search(r'[\u0E00-\u0E7F]', token) else [token]
        for piece in pieces:
            piece = piece.strip('_- ')
            if len(piece) >= 2 and piece not in STOP_WORDS and piece not in terms:
                terms.append(piece)
    return QueryPlan(normalized, project, tuple(sorted(set(t.upper() for t in TASK_RE.findall(normalized)) | ({task.upper()} if task else set()))),
                     tuple(k for k, needles in INTENT_TERMS.items()
                           if any(n in normalized.lower() for n in needles)) or ('status',),
                     tuple(terms[:12]))


@contextmanager
def open_readonly(database):
    """Open an existing file using SQLite mode=ro and query_only defenses."""
    uri = Path(database).expanduser().resolve().as_uri() + '?mode=ro'
    connection = sqlite3.connect(uri, uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('PRAGMA busy_timeout=10000')
        # One snapshot across candidates and their provenance, even during sync.
        connection.execute('BEGIN')
        yield connection
    finally:
        connection.close()


def _infer_project(c, plan):
    if plan.project is not None:
        return plan.project
    if plan.task_refs:
        marks = ','.join('?' for _ in plan.task_refs)
        for table, field, order, extra in (
            ('memory_items', 'scope_key', 'updated_at DESC, id DESC', "AND status='active'"),
            ('observation_events', 'task_ref', 'occurred_at DESC, id DESC', '')):
            row = c.execute(f'SELECT project FROM {table} WHERE upper({field}) IN ({marks}) '
                            f'AND project IS NOT NULL {extra} ORDER BY {order} LIMIT 1', plan.task_refs).fetchone()
            if row:
                return row['project']
    projects = c.execute('SELECT project FROM memory_items WHERE project IS NOT NULL '
                         'UNION SELECT project FROM observation_events WHERE project IS NOT NULL '
                         "UNION SELECT scope_key FROM memory_summaries WHERE scope='project'").fetchall()
    for name in sorted((r[0] for r in projects if r[0]), key=lambda p: (-len(p), p)):
        if name.lower() in plan.query.lower():
            return name
    return None


def _decode(row):
    value = dict(row)
    value['metadata'] = json.loads(value.get('metadata') or '{}')
    return value


def _score(candidate, plan):
    text = candidate['content'].lower()
    matched = sum(term in text for term in plan.terms)
    kind = candidate['kind']
    score = (25 if plan.query.lower() in text else 0) + matched * 4 + matched * matched * 3
    score += sum(24 for task in plan.task_refs if task.lower() in text)
    score += KIND_WEIGHTS.get(kind, 2) + float(candidate.get('confidence', 0.6)) * 5
    if str(candidate.get('task_ref') or candidate.get('scope_key') or '').upper() in plan.task_refs:
        score += 28
    if plan.project and candidate.get('project') == plan.project:
        score += 12
    if kind in plan.intents:
        score += 18
    if 'status' in plan.intents and kind in ('open_loop', 'fact', 'summary'):
        score += 8
    value = candidate.get('updated_at') or candidate.get('occurred_at')
    try:
        instant = datetime.fromisoformat(value.replace('Z', '+00:00'))
        instant = instant.replace(tzinfo=timezone.utc) if instant.tzinfo is None else instant
        age = max(0, (datetime.now(timezone.utc) - instant).total_seconds() / 86400)
        score += max(0, 6 - min(age, 30) / 5)
    except (ValueError, TypeError, AttributeError):
        pass
    candidate['matched_terms'] = matched
    candidate['score'] = round(score, 3)


def _match_clause(plan, task_field):
    clauses, params = [], []
    for term in dict.fromkeys((plan.query.lower(), *plan.terms, *(t.lower() for t in plan.task_refs))):
        if term:
            clauses.append('instr(lower(content),?)>0')
            params.append(term)
    if plan.task_refs:
        clauses.append(f'upper({task_field}) IN ({",".join("?" for _ in plan.task_refs)})')
        params.extend(plan.task_refs)
    return '(' + (' OR '.join(clauses) or '0') + ')', params


def recall(database, query, *, project=None, task=None, limit=12):
    """Return query, query_plan, ranked results, and referenced event evidence.

    Evidence has source_type/source_id (the selected parent), memory_id or
    summary_id, event_id, and the original observation provenance fields.
    Summary metadata event_ids/source_event_ids are expanded when present.
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ValueError('limit must be a positive integer')
    plan = analyze_query(query, project=project, task=task)
    if not plan.query:
        raise ValueError('query must not be empty')
    pool = max(limit * 5, 40)
    with open_readonly(database) as c:
        # Shared schema contract; deliberately do not call backend._connect.
        assert {'memory_items', 'memory_summaries', 'observation_events'} <= set(TABLES)
        plan = replace(plan, project=_infer_project(c, plan))
        match, args = _match_clause(plan, 'scope_key')
        scopes, scope_args = ["scope='global'"], []
        if plan.project is None and not plan.task_refs:
            # Without scope signals, search durable memories across projects.
            scopes.append('1')
        if plan.project is not None:
            scopes.append('(project=? OR scope_key=?)')
            scope_args.extend((plan.project, plan.project))
        if plan.task_refs:
            scopes.append(f'upper(scope_key) IN ({",".join("?" for _ in plan.task_refs)})')
            scope_args.extend(plan.task_refs)
        durable = [_decode(r) for r in c.execute(
            "SELECT *, 'durable' AS source_type FROM memory_items WHERE status='active' AND ("
            + ' OR '.join(scopes) + ') AND ' + match + ' ORDER BY updated_at DESC,id DESC LIMIT ?',
            (*scope_args, *args, pool))]
        summary_scopes, summary_args = [], []
        if plan.project is not None:
            summary_scopes.append("(scope='project' AND scope_key=?)")
            summary_args.append(plan.project)
        if plan.task_refs:
            summary_scopes.append(f"(scope='task' AND upper(scope_key) IN ({','.join('?' for _ in plan.task_refs)}))")
            summary_args.extend(plan.task_refs)
        summaries = [_decode(r) for r in c.execute(
            "SELECT *, 'summary' AS source_type, 'summary' AS kind, 1.0 AS confidence, created_at AS updated_at "
            'FROM memory_summaries WHERE ' + (' OR '.join(summary_scopes) or '0')
            + ' ORDER BY created_at DESC,id DESC LIMIT ?', (*summary_args, pool))]
        for row in summaries:
            row['project'] = row['scope_key'] if row['scope'] == 'project' else plan.project
        match, args = _match_clause(plan, 'task_ref')
        # Quote every token; never expose FTS operators from untrusted query text.
        # unicode61 normalization complements Thai/literal substring fallback.
        if plan.terms and c.execute("SELECT 1 FROM sqlite_master WHERE name='events_fts'").fetchone():
            fts = ' OR '.join('"' + token.replace('"', '""') + '"' for token in plan.terms)
            match = '(' + match + ' OR id IN (SELECT rowid FROM events_fts WHERE events_fts MATCH ?))'
            args.append(fts)
        events = [_decode(r) for r in c.execute(
            "SELECT *, 'event' AS source_type, 'session' AS scope, source_session_id AS scope_key, "
            '0.6 AS confidence, occurred_at AS updated_at FROM observation_events '
            f"WHERE (? IS NULL OR project=?) AND kind IN ({','.join('?' for _ in EVENT_KINDS)}) "
            'AND ' + match + ' ORDER BY occurred_at DESC,id DESC LIMIT ?',
            (plan.project, plan.project, *EVENT_KINDS, *args, pool))]
        candidates = durable + summaries + events
        for row in candidates:
            _score(row, plan)
        candidates.sort(key=lambda r: (r['source_type'] != 'event', r['score'],
                                      r.get('updated_at') or '', r['id']), reverse=True)
        selected = [r for r in candidates if r['source_type'] == 'summary' or
                    len(plan.terms) < 2 or plan.task_refs or r['matched_terms'] >= 2][:limit]
        evidence = []
        for row in selected:
            if row['source_type'] == 'durable':
                rows = c.execute('SELECT e.* FROM memory_sources ms JOIN observation_events e '
                                 'ON e.id=ms.event_id WHERE ms.memory_id=? '
                                 'ORDER BY e.occurred_at DESC,e.id DESC', (row['id'],))
            elif row['source_type'] == 'summary':
                meta = row['metadata'] if isinstance(row['metadata'], dict) else {}
                ids = [*(meta.get('event_ids') or []), *(meta.get('batch_event_ids') or [])]
                refs = meta.get('source_event_ids', [])
                ids = [v for v in ids if isinstance(v, int) and not isinstance(v, bool)] if isinstance(ids, list) else []
                # The sibling summarizer cites integer observation IDs here;
                # imported metadata may instead cite native transcript strings.
                refs = refs if isinstance(refs, list) else []
                ids.extend(v for v in refs if isinstance(v, int) and not isinstance(v, bool))
                native_ids = [v for v in refs if isinstance(v, str)]
                # JSON parameters avoid SQLite variable limits for large summaries.
                rows = c.execute('SELECT * FROM observation_events WHERE '
                                 'id IN (SELECT value FROM json_each(?)) OR '
                                 'source_event_id IN (SELECT value FROM json_each(?)) '
                                 'ORDER BY occurred_at DESC,id DESC', (json.dumps(ids), json.dumps(native_ids)))
            else:
                continue
            for source in rows:
                e = _decode(source)
                e['event_id'] = e['id']
                e['source_type'], e['source_id'] = row['source_type'], row['id']
                e['memory_id' if row['source_type'] == 'durable' else 'summary_id'] = row['id']
                evidence.append(e)
    return {'query': plan.query, 'query_plan': asdict(plan),
            'candidate_counts': {'durable': len(durable), 'summaries': len(summaries), 'events': len(events)},
            'results': selected, 'evidence': evidence}


# Familiar name for callers migrating from PostgreSQL reasoning.py.
reason_retrieve = recall


def main(argv=None):
    parser = argparse.ArgumentParser(description=__import__('brand').description(__doc__),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('database')
    parser.add_argument('query')
    parser.add_argument('--project')
    parser.add_argument('--task')
    parser.add_argument('--limit', type=int, default=12)
    args = parser.parse_args(argv)
    try:
        result = recall(args.database, args.query, project=args.project, task=args.task, limit=args.limit)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
