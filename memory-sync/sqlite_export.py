"""Deterministic complete SQLite projection. Explicit output root; DB read-only."""
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from recall_memory import open_readonly, _decode
from bounded_digest import run_lock


def name(value):
    value = str(value or 'Global')
    safe = re.sub(r'[^\w .-]', '-', value).strip(' .')[:100] or 'unnamed'
    # Hash full natural identity, including peer/source paths; not numeric prefixes.
    return safe + ' ' + hashlib.sha256(value.encode()).hexdigest()[:16]


def text(value):
    # Stored prose is data, not generated navigation. Preserve readable characters.
    return str(value or '').replace('[[', r'\[[')


def link(path, label=None, event=None):
    return '[[OMP Memory/' + path + (f'#^event-{event}' if event is not None else '') + '|' + text(label or path).replace('|', '-') + ']]'


def check_links(root):
    notes = {str(p.relative_to(root).with_suffix('')):p.read_text(encoding='utf-8') for p in Path(root).rglob('*.md')}
    anchors = {path: set(re.findall(r'(?m)^\^(event-\d+)$', contents)) for path, contents in notes.items()}
    checked = sources = 0
    for contents in notes.values():
        for target in re.findall(r'(?<!\\)\[\[([^\]|]+)(?:\|[^\]]*)?\]\]', contents):
            if not target.startswith('OMP Memory/'):
                continue
            path, _, anchor = target[len('OMP Memory/'):].partition('#')
            checked += 1
            if path not in notes or (anchor and anchor.lstrip('^') not in anchors[path]):
                raise ValueError('broken generated source/navigation link')
            sources += bool(anchor)
    return dict(links_checked=checked, source_links_checked=sources, broken_links=0)


def projection(database):
    with open_readonly(database) as c:
        events = [_decode(r) for r in c.execute('SELECT * FROM observation_events ORDER BY source_path,occurred_at,id')]
        items = [_decode(r) for r in c.execute("SELECT * FROM memory_items WHERE status='active' ORDER BY project,kind,memory_key,id")]
        # Retain current-memory note identities after the last item is resolved.
        # Historical source/summary notes are not pruned; no filesystem deletion.
        memory_projects = [r[0] or 'Global' for r in c.execute('SELECT DISTINCT project FROM memory_items')]
        summaries = [_decode(r) for r in c.execute('SELECT * FROM memory_summaries ORDER BY scope,scope_key,version,id')]
        refs = defaultdict(list)
        for r in c.execute('SELECT memory_id,event_id FROM memory_sources ORDER BY memory_id,event_id'):
            refs[r[0]].append(r[1])
    notes = {}
    def note(path, kind, title, lines):
        notes[path + '.md'] = '\n'.join(['---', 'generated: true', 'generator: agentmesh-sqlite-v1',
            'source_of_truth: SQLite (explicit backend)', 'type: ' + kind, '---', '', '# ' + text(title), '', *lines]) + '\n'
    sessions, projects, tasks = defaultdict(list), defaultdict(list), defaultdict(list)
    locations = {}
    for e in events:
        identity = e['source_path']
        path = 'Sessions/' + name(identity)
        locations[e['id']] = path
        sessions[identity].append(e)
        projects[e['project'] or 'unknown'].append(e)
        observed_tasks = set(re.findall(r'\b[A-Z][A-Z0-9]+-\d+\b', e['content'])) if e['kind'] in ('user_request','assistant_response') else set()
        if e['task_ref']:
            observed_tasks.add(e['task_ref'])
        for task in sorted(observed_tasks):
            tasks[task].append(e)
    def source(eid):
        if eid not in locations:
            raise ValueError('missing evidence source')
        return link(locations[eid], f'event {eid}', eid)
    for identity, values in sorted(sessions.items()):
        lines = ['Source path: `' + text(identity) + '`', '']
        for e in values:
            lines += [f"## {e['kind']} | {e['occurred_at'] or 'unknown time'} | event {e['id']}", '',
                      f"Agent: {text(e['source_agent'])} | Project: {text(e['project'])} | Native ID: {text(e['source_event_id'])}",
                      '', text(e['content']), '', f"^event-{e['id']}", '']
        note('Sessions/' + name(identity), 'source-session', identity, lines)
    for project, values in sorted(projects.items()):
        note('Projects/' + name(project), 'project', project,
             [*[link('Tasks/' + name(task), task) for task, task_events in sorted(tasks.items())
                if any((e['project'] or 'unknown') == project for e in task_events)],
              *[link('Sessions/' + name(path), path) for path in sorted({e['source_path'] for e in values})],
              *[source(e['id']) for e in values]])
    for task, values in sorted(tasks.items()):
        note('Tasks/' + name(task), 'task', task, [source(e['id']) for e in values])
    grouped = defaultdict(list)
    grouped['Global'] = []
    for project in memory_projects:
        grouped[project] = []
    for i in items:
        grouped[i['project'] or 'Global'].append(i)
    for project, values in sorted(grouped.items()):
        lines = []
        for i in values:
            lines += [f"## {i['kind']} | {text(i['memory_key'] or i['id'])}", '', text(i['content']), '',
                      f"Confidence: {i['confidence']}", 'Sources: ' + ', '.join(source(eid) for eid in refs[i['id']]), '']
        note('Memory/' + name(project), 'durable-memory', project + ' Durable Memory', lines)
    for s in summaries:
        identity = f"{s['scope']} {s['scope_key']} v{s['version']}"
        md = s['metadata']
        ids = set(v for field in ('source_event_ids','event_ids','batch_event_ids')
                  for v in (md.get(field) or []) if type(v) is int and v in locations)
        lines = [text(s['content']), '', 'Sources: ' + ', '.join(source(eid) for eid in sorted(ids))]
        note('Summaries/' + name(identity), 'memory-summary', identity, lines)
    note('Preferences', 'preferences', 'Active preferences', [text(i['content']) + '\n' + ', '.join(source(eid) for eid in refs[i['id']]) for i in items if i['kind']=='preference'])
    note('_Index', 'index', 'Shared Memory', [f'Events: {len(events)} | Active items: {len(items)} | Summaries: {len(summaries)}', '',
         *[link(path[:-3]) for path in sorted(notes)]])
    return notes, dict(events=len(events), items=len(items), summaries=len(summaries), sessions=len(sessions), tasks=len(tasks), projects=len(projects))


def publish(database, root):
    root = Path(root).expanduser().absolute()
    # Reject symlink ancestors before resolving or creating any output.
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError('symlink export root refused')
    root.parent.mkdir(parents=True, exist_ok=True)
    with run_lock(str(root) + '.export'), tempfile.TemporaryDirectory(prefix='.agentmesh-export-', dir=root.parent) as temp:
        staged = Path(temp)
        notes, stats = projection(database)
        for path, contents in notes.items():
            out = staged / path
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(contents, encoding='utf-8')
        stats.update(check_links(staged))
        # Preflight the ENTIRE publish before replacing any notes. Manual notes survive.
        for path in notes:
            dest = root / path
            if any(p.is_symlink() for p in (dest, *dest.parents)):
                raise ValueError('symlink export target refused')
            if dest.exists() and not dest.read_text(encoding='utf-8').startswith('---\ngenerated: true\ngenerator: agentmesh-sqlite-v1\n'):
                raise ValueError('manual projection note collision')
        for path in notes:
            dest = root / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged / path, dest)
        stats.update(files_published=len(notes))
        # All newly generated links verified before publish, then exact root read-back.
        stats.update(check_links(root))
        return stats


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('database')
    p.add_argument('--root', required=True)
    args = p.parse_args(argv)
    print(json.dumps(publish(args.database, args.root)))


if __name__ == '__main__':
    main()
