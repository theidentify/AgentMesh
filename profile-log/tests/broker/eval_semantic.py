"""Owner-run recall evaluation; keep the UTF-8 query file private and uncommitted.

Run with the prepared interpreter, like bench_broker.py. Top-5 comparisons use
the last run; timings, retrieval metadata and cosine counts include every run.
Percentiles use linear interpolation. Histogram buckets are left-closed, 0.05
wide, with cosine 1.0 included in the final bucket. No live eval runs in pytest.
"""
import argparse
from decimal import Decimal, ROUND_FLOOR
import json
import math
from pathlib import Path
import subprocess
import sys
import time


MODES = ('keyword', 'hybrid')


def percentile(values, percent):
    """Linearly interpolate a percentile of a nonempty sample."""
    if not values or not 0 <= percent <= 100:
        raise ValueError('invalid percentile sample')
    ordered = sorted(values)
    position = (len(ordered)-1)*percent/100
    low = math.floor(position)
    high = math.ceil(position)
    return ordered[low] + (ordered[high]-ordered[low])*(position-low)


def top_ids(results):
    """Return the first five IDs in ranking order."""
    return [row['id'] for row in results[:5]]


def new_ids(keyword, hybrid):
    """Return hybrid top-five additions with only their ID and cosine."""
    previous = set(top_ids(keyword))
    return [dict(id=row['id'], cosine=row.get('ranking', {}).get('cosine'))
            for row in hybrid[:5] if row['id'] not in previous]


def histogram(cosines):
    """Count finite cosines in occupied 0.05 buckets across [-1, 1]."""
    counts = {}
    for value in cosines:
        if value is None or not math.isfinite(value) or not -1 <= value <= 1:
            continue
        bucket = min(19, int((Decimal(str(value))*20).to_integral_value(rounding=ROUND_FLOOR)))
        counts[bucket] = counts.get(bucket, 0)+1
    return {f'{bucket/20:.2f}..{(bucket+1)/20:.2f}': counts[bucket]
            for bucket in sorted(counts)}


def latencies(samples):
    return dict(p50_ms=round(percentile(samples, 50), 3),
                p95_ms=round(percentile(samples, 95), 3))


def positive(value):
    number = int(value)
    if number < 1: raise argparse.ArgumentTypeError('must be positive')
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--queries', required=True, type=Path)
    parser.add_argument('--agent-key', required=True)
    parser.add_argument('--profile')
    parser.add_argument('--runs', type=positive, default=5)
    parser.add_argument('--limit', type=positive, default=12)
    parser.add_argument('--python', default=sys.executable)
    args = parser.parse_args(argv)
    try:
        queries = [line.strip() for line in args.queries.read_text(encoding='utf-8').splitlines()
                   if line.strip() and not line.lstrip().startswith('#')]
    except (OSError, UnicodeError):
        print('Cannot read query file.', file=sys.stderr)
        return 1
    if not queries:
        print('Query file has no queries.', file=sys.stderr)
        return 1
    overall = {mode: [] for mode in MODES}
    cosines = []
    degraded = 0
    for index, query in enumerate(queries, 1):
        report = dict(query_index=index)
        last = {}
        for mode in MODES:
            samples, retrievals = [], []
            command = [args.python, '-m', 'agentmesh_log.cli', 'recall', query,
                       '--compat', '--agent-key', args.agent_key, '--limit', str(args.limit),
                       '--mode', mode]
            if args.profile: command += ['--profile', args.profile]
            for run in range(1, args.runs+1):
                try:
                    start = time.perf_counter()
                    completed = subprocess.run(command, stdin=subprocess.DEVNULL,
                        capture_output=True, text=True, encoding='utf-8', timeout=60,
                        cwd=Path(__file__).resolve().parents[2], check=True)
                    elapsed = (time.perf_counter()-start)*1000
                    answer = json.loads(completed.stdout)
                    results = answer['results']
                    # Project only report fields: never echo stdout/stderr or exceptions.
                    last[mode] = [dict(id=row['id'], ranking=row.get('ranking', {}))
                                  for row in results]
                    retrieval = answer.get('query_plan', {}).get('retrieval', {})
                    info = {field: retrieval.get(field) for field in ('effective', 'degraded', 'coverage')}
                    if not retrieval and mode == 'keyword': info['effective'] = 'keyword'
                    if info['degraded']: degraded += 1
                    retrievals.append(info)
                    for row in results:
                        ranking = row.get('ranking', {})
                        if ranking.get('semantic_rank') is not None:
                            cosines.append(ranking.get('cosine'))
                except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, AttributeError):
                    print(f'Recall failed: query index {index}, mode {mode}, run {run}.', file=sys.stderr)
                    return 1
                samples.append(elapsed)
                overall[mode].append(elapsed)
            report[mode] = dict(top5_ids=top_ids(last[mode]), **latencies(samples),
                                retrieval=retrievals)
        report['new_in_hybrid_top5'] = new_ids(last['keyword'], last['hybrid'])
        print(json.dumps(report), flush=True)
    summary = dict(summary=True, **{mode: latencies(overall[mode]) for mode in MODES},
                   delta_p95_ms=round(percentile(overall['hybrid'], 95)-percentile(overall['keyword'], 95), 3),
                   degraded_runs=degraded, cosine_histogram=histogram(cosines))
    print(json.dumps(summary), flush=True)
    print('\nmode       runs       p50_ms       p95_ms')
    for mode in MODES:
        stats = summary[mode]
        print(f"{mode:<10} {len(overall[mode]):>4} {stats['p50_ms']:>12.3f} {stats['p95_ms']:>12.3f}")
    print(f"delta p95 (hybrid - keyword) ms: {summary['delta_p95_ms']:.3f}")
    print(f'Degraded runs: {degraded}')
    return 0


if __name__ == '__main__': sys.exit(main())
