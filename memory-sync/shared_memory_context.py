"""Read-only SQLite compatibility entrypoint; no caller rules are installed."""
import argparse
import json
import os

from recall_memory import open_readonly, _decode
from summarize_memory import DEFAULT_CONSUMER


# Reused by context, scheduler, and disabled retention audit. Never a scalar cursor.
COVERAGE_SQL = '''NOT EXISTS(SELECT 1 FROM summary_state s
    WHERE s.consumer=? || ':event:' || CAST(e.id AS TEXT))
    AND NOT EXISTS(SELECT 1 FROM memory_items m,json_each(m.metadata,'$.batch_event_ids') j
    WHERE json_extract(m.metadata,'$.consumer')=? AND j.value=e.id)
    AND NOT EXISTS(SELECT 1 FROM memory_summaries m,json_each(m.metadata,'$.batch_event_ids') j
    WHERE json_extract(m.metadata,'$.consumer')=? AND j.value=e.id)'''


def pending_ids(c, consumer=DEFAULT_CONSUMER):
    return [r[0] for r in c.execute("SELECT e.id FROM observation_events e WHERE e.kind IN ('user_request','assistant_response') AND "
                                   + COVERAGE_SQL + ' ORDER BY e.id', (consumer,) * 3)]


def context(database, *, project=None, task=None, query=None, limit=20, consumer=DEFAULT_CONSUMER):
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('limit outside bounds')
    with open_readonly(database) as c:
        summaries = [_decode(r) for r in c.execute('''SELECT * FROM memory_summaries WHERE
            (? IS NULL AND ? IS NULL) OR (scope='project' AND scope_key=?) OR (scope='task' AND scope_key=?)
            ORDER BY created_at DESC,id DESC LIMIT 5''', (project, task, project, task))]
        items = [_decode(r) for r in c.execute('''SELECT * FROM memory_items WHERE status='active' AND
            (scope='global' OR project=? OR scope_key=? OR (scope='task' AND upper(scope_key)=upper(?)))
            AND (? IS NULL OR instr(lower(content),lower(?))>0)
            ORDER BY CASE kind WHEN 'constraint' THEN 1 WHEN 'open_loop' THEN 2 WHEN 'decision' THEN 3
            WHEN 'preference' THEN 4 WHEN 'procedure' THEN 5 ELSE 6 END,confidence DESC,updated_at DESC,id DESC LIMIT ?''',
            (project, project, task, query, query, limit))]
        for i in items:
            i['sources'] = [dict(r) for r in c.execute('''SELECT e.id,e.source_path,e.source_event_id,e.occurred_at
                FROM memory_sources ms JOIN observation_events e ON e.id=ms.event_id WHERE ms.memory_id=?
                ORDER BY e.occurred_at DESC,e.id DESC LIMIT 5''', (i['id'],))]
        recent = [_decode(r) for r in c.execute("""SELECT e.* FROM observation_events e WHERE
            e.kind IN ('user_request','assistant_response') AND (? IS NULL OR e.project=?)
            AND (? IS NULL OR upper(e.task_ref)=upper(?) OR instr(upper(e.content),upper(?))>0) AND """
            + COVERAGE_SQL + ' ORDER BY e.occurred_at DESC,e.id DESC LIMIT 20',
            (project, project, task, task, task, *(consumer,) * 3))]
    lines = ['# Shared Memory Context', '', 'Backend: SQLite (explicit selection; no production authority switch implied).', '', '## Summaries']
    for s in summaries:
        lines += ['', f"### {s['scope']}: {s['scope_key']}", s['content']]
    lines += ['', '## Active durable memory']
    for i in items:
        lines += ['', f"### {i['kind']} | {i['memory_key']}", i['content'],
                  'Sources: ' + ', '.join(f"event {r['id']} ({r['source_event_id'] or 'no-source-id'})" for r in i['sources'])]
    if not items:
        lines += ['', 'No matching durable memory.']
    if recent:
        lines += ['', '## Recent observations awaiting summarization', '', 'Coverage uses explicit event receipts/provenance, not numeric ID order.']
        for e in reversed(recent):
            lines.append(f"- event {e['id']} | {e['project']} | {e['kind']}: " + ' '.join(e['content'].split())[:500])
    return dict(summaries=summaries, items=items, recent_unsummarized=recent, markdown='\n'.join(lines))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database', default=os.environ.get('AGENTMESH_DATABASE'))
    for name in ('project', 'task', 'query'):
        p.add_argument('--' + name)
    p.add_argument('--limit', type=int, default=20)
    p.add_argument('--consumer', default=DEFAULT_CONSUMER)
    p.add_argument('--json', action='store_true')
    args = p.parse_args(argv)
    if not args.database:
        p.error('--database or AGENTMESH_DATABASE required')
    result = context(args.database, project=args.project, task=args.task, query=args.query, limit=args.limit, consumer=args.consumer)
    print(json.dumps(result, ensure_ascii=False) if args.json else result['markdown'])


if __name__ == '__main__':
    main()
