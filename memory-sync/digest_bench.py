"""Benchmark digest models through digest_api.py on fixed fixture batches.

Each case is {name, events, related}. Every model gets the identical bounded
request; outputs are checked with bounded_protocol.validate. Reports validity,
memory_key stability across trials, tokens, cost and latency per model.

  python digest_bench.py --models claude-code:claude-haiku-5-5 claude-code:claude-sonnet-5-5 \
      codex:gpt-6-luna --trials 3 --out bench.jsonl
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys
import time

from bounded_protocol import build_request, validate
from digest_api_client import digest
from memory_sync import canonical

HERE = Path(__file__).resolve().parent
DEFAULT_FIXTURES = HERE / 'bench' / 'digest_cases.json'


def load_cases(path):
    cases = json.loads(Path(path).read_text(encoding='utf-8'))
    for case in cases:
        if set(case) - {'name', 'events', 'related', 'note'} or not case.get('events'):
            raise ValueError('invalid case ' + repr(case.get('name')))
    return cases


def run_case(call, model, case, context_chars=80000):
    """One trial; never raises for model failures so the run continues."""
    related = case.get('related', [])
    request = build_request(case['events'], related, context_chars)
    start = time.monotonic()
    row = dict(model=model, case=case['name'], valid=False, error=None, keys=[], items=0,
               cost_usd=None, input_tokens=None, output_tokens=None)
    try:
        result = call(model, request)
        row.update(cost_usd=result['usage'].get('actual_cost_usd'),
                   input_tokens=result['usage'].get('input_total_tokens'),
                   output_tokens=result['usage'].get('output_tokens'))
        validate(result['payload'], case['events'], related)
        row.update(valid=True, items=len(result['payload']['items']),
                   keys=sorted(i['memory_key'] for i in result['payload']['items']))
    except Exception as exc:
        row['error'] = '%s: %s' % (type(exc).__name__, str(exc)[:120])
    row['seconds'] = round(time.monotonic() - start, 2)
    return row


def summarize(rows):
    by_model = defaultdict(list)
    for row in rows:
        by_model[row['model']].append(row)
    report = []
    for model, items in by_model.items():
        valid = [r for r in items if r['valid']]
        mean = lambda key: (sum(r[key] for r in items if r[key] is not None) /
                            max(1, sum(r[key] is not None for r in items)))
        # Stability: share of cases whose valid trials all produced the same key set.
        per_case = defaultdict(set)
        for r in valid:
            per_case[r['case']].add(tuple(r['keys']))
        stable = sum(len(v) == 1 for v in per_case.values())
        costs = [r['cost_usd'] for r in items if r['cost_usd'] is not None]
        report.append(dict(model=model, trials=len(items), valid=len(valid),
                           key_stable_cases='%d/%d' % (stable, len(per_case)),
                           mean_cost_usd=round(mean('cost_usd'), 5) if costs else None,
                           total_cost_usd=round(sum(costs), 4) if costs else None,
                           mean_in=round(mean('input_tokens')), mean_out=round(mean('output_tokens')),
                           mean_seconds=round(mean('seconds'), 1)))
    return report


def print_table(report, stream=sys.stdout):
    cols = ['model', 'valid', 'trials', 'key_stable_cases', 'mean_cost_usd', 'total_cost_usd',
            'mean_in', 'mean_out', 'mean_seconds']
    widths = {c: max(len(c), *(len(str(r[c])) for r in report)) for c in cols}
    print('  '.join(c.ljust(widths[c]) for c in cols), file=stream)
    for r in report:
        print('  '.join(str(r[c]).ljust(widths[c]) for c in cols), file=stream)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--url', default='http://127.0.0.1:8765')
    p.add_argument('--models', nargs='+', required=True, help='provider:model specs')
    p.add_argument('--fixtures', default=str(DEFAULT_FIXTURES))
    p.add_argument('--trials', type=int, default=2)
    p.add_argument('--context-chars', type=int, default=80000)
    p.add_argument('--token-file')
    p.add_argument('--out', help='append per-trial rows as JSONL')
    args = p.parse_args(argv)
    token = Path(args.token_file).read_text(encoding='utf-8').strip() if args.token_file else None
    call = lambda model, request: digest(args.url, model, request, token=token)
    rows = []
    for case in load_cases(args.fixtures):
        for model in args.models:
            for trial in range(args.trials):
                row = dict(run_case(call, model, case, args.context_chars), trial=trial)
                rows.append(row)
                print(canonical({k: row[k] for k in ('model', 'case', 'trial', 'valid', 'error', 'seconds')}),
                      file=sys.stderr, flush=True)
                if args.out:
                    with open(args.out, 'a', encoding='utf-8') as handle:
                        handle.write(canonical(row) + '\n')
    print_table(summarize(rows))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
