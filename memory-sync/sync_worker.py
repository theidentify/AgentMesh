"""Periodic local sync worker with privacy-limited peer status acknowledgments."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import sqlite3
import time
import uuid

import memory_sync
import sqlite_memory


def run_once(database, exchange, postgres_dsn=None):
    exchange = Path(exchange)
    if not (exchange / '.stfolder').exists():
        raise ValueError('exchange is not an accepted Syncthing folder')
    mirror = None
    if postgres_dsn:
        import pg_mirror
        try:
            mirror = pg_mirror.refresh(database, postgres_dsn)
        except Exception as exc:
            # Never emit driver messages, DSNs or memory contents.
            mirror = {'error': type(exc).__name__}
    cycle = memory_sync.cycle(database, exchange)
    state = memory_sync.status(database)
    report = {'format': 'agentmesh-status-v1', 'node': state['node'],
              'group': state['group_id'], 'platform': platform.system(),
              'updated_at': datetime.now(timezone.utc).isoformat(),
              'counts': sqlite_memory.status(database)['counts'],
              'sync': state, 'cycle': cycle, 'postgres_mirror': mirror}
    directory = exchange / 'status'
    directory.mkdir(exist_ok=True)
    temp = directory / ('.' + state['node'] + '-' + str(uuid.uuid4()) + '.tmp')
    try:
        with temp.open('x', encoding='utf-8') as out:
            json.dump(report, out, ensure_ascii=False, sort_keys=True)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, directory / (state['node'] + '.json'))
    finally:
        temp.unlink(missing_ok=True)
    return report


def main(argv=None):
    import argparse
    import sys
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database')
    parser.add_argument('exchange')
    parser.add_argument('--interval', type=float, default=60)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--postgres', action='store_true', help='read-only PostgreSQL mirror using OMP_MEMORY_DSN')
    args = parser.parse_args(argv)
    if args.interval <= 0:
        parser.error('interval must be positive')
    dsn = os.environ.get('OMP_MEMORY_DSN') if args.postgres else None
    if args.postgres and not dsn:
        parser.error('--postgres requires OMP_MEMORY_DSN')
    try:
        while True:
            try:
                report = run_once(args.database, args.exchange, dsn)
            except Exception as exc:
                report = {'error': type(exc).__name__}
            print(json.dumps(report, ensure_ascii=False), flush=True)
            if args.once:
                return int('error' in report)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
