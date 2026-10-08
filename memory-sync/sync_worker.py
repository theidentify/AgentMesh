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


def run_once(database, exchange, postgres_dsn=None, *, workflow_config=None, force_summary=False,
             force_legacy_summary=False, progress=None, security_dir=None):
    exchange = Path(exchange)
    if not (exchange / '.stfolder').exists():
        raise ValueError('exchange is not an accepted Syncthing folder')
    security = None
    if security_dir is not None:
        from signed_packets import Security
        security = Security(security_dir)
    # Fail closed before mirror/ingestion/summary can write after signed activation.
    with memory_sync.connect(database) as c, c:
        c.execute('BEGIN IMMEDIATE')
        memory_sync._security_policy(c, security, exchange)
    mirror = None
    if postgres_dsn:
        import pg_mirror
        try:
            mirror = pg_mirror.refresh(database, postgres_dsn)
        except Exception as exc:
            # Never emit driver messages, DSNs or memory contents.
            mirror = {'error': type(exc).__name__}
    workflow_report = None
    settings = None
    try:
        import workflow
        settings = workflow.load(database, workflow_config)
        if settings is not None:
            if progress:
                progress('Ingesting local observations')
            workflow_report = {'ingestion': workflow.ingest(database, settings,
                                postgres_mirror=bool(postgres_dsn))}
    except Exception as exc:
        workflow_report = {'ingestion': {'error': type(exc).__name__}}
    if progress:
        progress('Publishing and receiving peer changes')
    cycle = memory_sync.cycle(database, exchange, security=security)
    if settings is not None:
        try:
            if progress:
                progress('Checking primary summarization')
            # Process startup is not an OS boot. Keep legacy startup behavior only.
            force = force_summary or (force_legacy_summary and settings.get('summary_backend', 'legacy') == 'legacy')
            result = workflow.summarize(database, settings, force=force)
            workflow_report['summary'] = result
            if result['status'] == 'committed':
                if progress:
                    progress('Publishing generated memory and provenance')
                workflow_report['summary_sync'] = memory_sync.cycle(database, exchange, security=security)
        except Exception as exc:
            workflow_report['summary'] = {'status': 'blocked', 'error': type(exc).__name__}
    state = memory_sync.status(database)
    security_report = dict(state['security'])
    wizard_path = Path(database).parent / 'security-wizard.json'
    if wizard_path.exists():
        try:
            import security_wizard
            wizard_state = security_wizard.load_state(wizard_path)
            if wizard_state is not None:
                projection = security_wizard.resume(database, exchange, security_dir or wizard_state['security_dir'], wizard_path, dry_run=True)
                security_report.update(projection)
        except Exception:
            # No private paths, key material or raw failures in status.
            security_report.update(pairing='unknown', wizard_step='unknown', next_action='recover_identity_or_receipt', roundtrip='pending')
    elif security is not None:
        from signed_packets import read_trust
        peers = [p for p in read_trust(security.directory)['peers'].values() if p['sender'] != security.public['sender'] and p['group'] == security.public['group']]
        security_report['pairing'] = 'approved' if any(not p['revoked'] for p in peers) else 'revoked' if peers else 'pending'
    report = {'format': 'agentmesh-status-v1', 'node': state['node'],
              'group': state['group_id'], 'platform': platform.system(),
              'updated_at': datetime.now(timezone.utc).isoformat(),
              'counts': sqlite_memory.status(database)['counts'],
              'sync': state, 'cycle': cycle, 'postgres_mirror': mirror,
              'security': security_report,
              'workflow': workflow_report}
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


def failed(report):
    workflow = report.get('workflow') or {}
    return bool('error' in report or
                (report.get('postgres_mirror') or {}).get('error') or
                (workflow.get('ingestion') or {}).get('error') or
                (workflow.get('ingestion') or {}).get('errors') or
                (workflow.get('summary') or {}).get('error') or
                (workflow.get('summary') or {}).get('status') in ('stale', 'blocked', 'failed') or
                report.get('sync', {}).get('conflict') or
                report.get('sync', {}).get('invalid'))


def main(argv=None):
    import argparse
    import sys
    parser = argparse.ArgumentParser(description=__import__('brand').description(__doc__),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('database')
    parser.add_argument('exchange')
    parser.add_argument('--interval', type=float, default=60)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--force-summary', action='store_true',
                        help='explicitly bypass the summary schedule on the first cycle')
    parser.add_argument('--workflow-config', help='private machine configuration; defaults beside DB')
    parser.add_argument('--security-dir', help='explicit strict signing and pinned peer trust')
    parser.add_argument('--postgres', action='store_true', help='read-only PostgreSQL mirror using OMP_MEMORY_DSN')
    args = parser.parse_args(argv)
    if args.interval <= 0:
        parser.error('interval must be positive')
    dsn = os.environ.get('OMP_MEMORY_DSN') if args.postgres else None
    if args.postgres and not dsn:
        parser.error('--postgres requires OMP_MEMORY_DSN')
    from terminal_progress import TerminalProgress
    first = True
    try:
        while True:
            with TerminalProgress() as progress:
                try:
                    progress('Starting sync cycle')
                    report = run_once(args.database, args.exchange, dsn,
                        workflow_config=args.workflow_config, force_summary=args.force_summary and first,
                        force_legacy_summary=first, progress=progress, security_dir=args.security_dir)
                    first = False
                except Exception as exc:
                    report = {'error': type(exc).__name__}
                state = report.get('sync', {})
                outcome = 'Sync needs attention' if failed(report) else 'Sync completed'
                progress(f"{outcome} | Pending {state.get('pending', 0)} | Conflicts {state.get('conflict', 0)} | Invalid {state.get('invalid', 0)}")
            print(json.dumps(report, ensure_ascii=False), flush=True)
            if args.once:
                return int(failed(report))
            with TerminalProgress() as progress:
                progress.wait(args.interval,
                    'Last sync needs attention' if failed(report) else 'Last sync succeeded')
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
