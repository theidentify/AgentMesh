"""Read-only before/after accounting for the OMP digest."""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

def render_table(data):
    before, after = data['baseline'], data['new']
    lines = ['Metric | Before (historical cron) | After (bounded runs)', '---|---:|---:',f"Counted runs | {before['sample_count']} | {after['sample_count']}"]
    for field in FIELDS:
        values = []
        for group in (before,after):
            value = group[field]['total']
            values.append('unknown' if value is None else f'{value:,.3f}' if isinstance(value,float) else f'{value:,}')
        lines.append(f"{field} | {values[0]} | {values[1]}")
    lines += ['',f"Matched baseline sessions: {data['baseline_matched_sessions']}/{data['baseline_sessions_available']}",f"Successful LLM runs: {data['new_successful_llm']['sample_count']}",'','Metric | Latest historical cron | Latest successful bounded LLM run','---|---:|---:']
    for field in ('llm_api_calls','input_total_tokens','input_uncached_tokens','cache_read_tokens','output_tokens','reasoning_tokens','duration_seconds','context_max_input_tokens','events_processed','accepted_items','actual_cost_usd'):
        values=[]
        for row in (data['baseline_latest'],data['new_latest_llm']):
            value=(row or {}).get(field)
            values.append('unknown' if value is None else f'{value:,.3f}' if isinstance(value,float) else f'{value:,}')
        lines.append(f'{field} | {values[0]} | {values[1]}')
    return '\n'.join(lines)


def main(argv=None):
    import argparse
    import os
    home = Path(os.environ.get('HERMES_HOME', str(Path.home()/'.hermes')))
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--audit',type=Path,default=home/'cron/usage_audit.jsonl')
    p.add_argument('--state',type=Path,default=home/'state.db')
    p.add_argument('--metrics-log',type=Path,default=home/'omp-memory/summary-metrics.jsonl')
    p.add_argument('--json',action='store_true')
    args = p.parse_args(argv)
    data = report(args.audit,args.state,args.metrics_log)
    print(json.dumps(data,ensure_ascii=False,indent=2) if args.json else render_table(data)+'\n\n'+'\n'.join(data['notes']))


FIELDS = ('llm_api_calls','input_total_tokens','input_uncached_tokens','cache_read_tokens','output_tokens','reasoning_tokens','actual_cost_usd','duration_seconds','events_processed','context_max_input_tokens','raw_chars','submitted_chars','accepted_items','rejected_items','upserted_items','exporter_duration_seconds')


def read_jsonl(path):
    if not Path(path).exists():
        return []
    rows = []
    for line in Path(path).read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))  # Fail explicitly on corruption; don't silently undercount.
    return rows


def aggregate(rows):
    result: dict = {'sample_count':len(rows)}
    for field in FIELDS:
        values = [r.get(field) for r in rows]
        known = [v for v in values if v is not None]
        result[field] = {'total':sum(known) if known and len(known)==len(rows) else None,'known_samples':len(known),'sample_count':len(rows)}
    result['statuses'] = {s:sum(r.get('status')==s for r in rows) for s in sorted({str(r.get('status')) for r in rows})}
    return result


def report(audit_path, state_path, metrics_path, job_id='6fcf5646d34d'):
    audits = [r for r in read_jsonl(audit_path) if r.get('job_id') == job_id]
    # Retries may log a fire more than once: count distinct audit records explicitly.
    audits = list({r.get('fire_id') or str(n):r for n,r in enumerate(audits)}.values())
    with sqlite3.connect(Path(state_path).resolve().as_uri() + '?mode=ro', uri=True) as c:
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA query_only=ON')
        c.execute('BEGIN')
        allowed = ('id','started_at','input_tokens','cache_read_tokens','reasoning_tokens','api_call_count','actual_cost_usd','model')
        available = {r['name'] for r in c.execute('PRAGMA table_info(sessions)')}
        columns = ','.join(name for name in allowed if name in available)
        sessions = [dict(r) for r in c.execute('SELECT ' + columns + ' FROM sessions WHERE id LIKE ? ORDER BY started_at', ('cron_'+job_id+'_%',))]
    baseline, matched = [], set()
    for a in audits:
        end = datetime.fromisoformat(a['ts'].replace('Z','+00:00')).timestamp()
        duration = a.get('duration_ms')
        start = end - duration/1000 if duration is not None else None
        candidates = [s for s in sessions if s['id'] not in matched and start is not None and abs(s['started_at']-start)<15]
        session = min(candidates, key=lambda s:abs(s['started_at']-start)) if candidates else None
        row = {'status':'error' if a.get('error') else 'success', 'input_total_tokens':a.get('prompt_tokens'), 'output_tokens':a.get('completion_tokens'), 'duration_seconds':duration/1000 if duration is not None else None,'actual_cost_usd':None}
        if session:
            matched.add(session['id'])
            row.update(input_uncached_tokens=session.get('input_tokens'),cache_read_tokens=session.get('cache_read_tokens'),reasoning_tokens=session.get('reasoning_tokens'),llm_api_calls=session.get('api_call_count'),actual_cost_usd=session.get('actual_cost_usd'),model=session.get('model'),session_id=session['id'])
        baseline.append(row)
    runs = list({r['run_id']:r for r in read_jsonl(metrics_path) if r.get('record_type')=='run'}.values())
    successful=[r for r in runs if r.get('status') in ('dry_run','replay','applied','committed','validated') and (r.get('llm_api_calls') or 0)>0]
    return {'baseline':aggregate(baseline),'baseline_matched':aggregate([r for r in baseline if r.get('session_id')]),'baseline_matched_sessions':len(matched),'baseline_sessions_available':len(sessions),
            'new_successful_llm':aggregate(successful),'new_latest_llm':successful[-1] if successful else None,
            'baseline_latest':baseline[-1] if baseline else None,'new':aggregate(runs),
            'new_by_status':{status:aggregate([r for r in runs if r.get('status')==status]) for status in sorted({r['status'] for r in runs})},
            'new_latest':runs[-1] if runs else None,
            'notes':['Historical prompt_tokens includes cached input; state.db input_tokens is uncached.','Unknown values remain null; known_samples gives metric coverage.','Quota/subscription token consumption is not actual dollar cost.','Replay/dry-run samples are not matched-workload production effectiveness benchmarks.','Historical maximum per-call context and event throughput unavailable from these sources.']}
