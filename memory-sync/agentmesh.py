"""Shared local-first AgentMesh CLI. Recall is read-only; source text is data."""
import argparse
import json
import os
from pathlib import Path
import sys

import sync_worker


def main(argv=None):
    parser = argparse.ArgumentParser(description=__import__('brand').description(__doc__),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--database', help='initialized local database; required except for inspect-install')
    parser.add_argument('--exchange')
    parser.add_argument('--config', help='private workflow.json; defaults beside DB')
    subs = parser.add_subparsers(dest='action', required=True)
    recall = subs.add_parser('recall', description=__import__('brand').description('Read-only recall with source evidence.'), formatter_class=argparse.RawDescriptionHelpFormatter)
    recall.add_argument('query')
    recall.add_argument('--project')
    recall.add_argument('--limit', type=int, default=12)
    ingest = subs.add_parser('ingest', description=__import__('brand').description('Incrementally ingest local agent sessions.'), formatter_class=argparse.RawDescriptionHelpFormatter)
    ingest.add_argument('--root', action='append', default=[])
    ingest.add_argument('--project')
    summary = subs.add_parser('summarize', description=__import__('brand').description('Generate provenance-grounded memory on the primary.'), formatter_class=argparse.RawDescriptionHelpFormatter)
    summary.add_argument('--preview', action='store_true', help='explicitly print private source task')
    for name in ('once', 'watch'):
        worker = subs.add_parser(name, description=__import__('brand').description('Ingest, exchange, and run configured primary summarization.'), formatter_class=argparse.RawDescriptionHelpFormatter)
        worker.add_argument('--postgres', action='store_true')
        if name == 'watch':
            worker.add_argument('--interval', type=float, default=60)
    subs.add_parser('status', description=__import__('brand').description('Inspect local memory and peer-sync state.'), formatter_class=argparse.RawDescriptionHelpFormatter)
    inspect = subs.add_parser('inspect-install', help='read-only discovery of an existing installation')
    inspect.add_argument('--runtime', help='existing runtime.json; default is the platform installation path')
    wizard = subs.add_parser('wizard-status', help='read-only security wizard status for an existing installation')
    wizard.add_argument('--runtime', help='existing runtime.json; default is the platform installation path')
    wizard = subs.add_parser('wizard-resume', help='interactive security setup for an existing installation only')
    wizard.add_argument('--runtime', help='existing runtime.json; default is the platform installation path')
    setup = subs.add_parser('setup-new', help='create a NEW EMPTY installation and isolated sync group')
    setup.add_argument('--local-dir', required=True, help='new absolute local root; parent must exist')
    setup.add_argument('--exchange', required=True, help='accepted absolute Syncthing exchange')
    setup.add_argument('--node', required=True, help='allocation slot: mac, windows or linux')
    adoption = subs.add_parser('adopt-install', help='bind existing paths only; requires BIND')
    for key in ('runtime', 'app-root', 'database', 'exchange', 'node', 'security-dir', 'security-state', 'workflow-config'):
        adoption.add_argument('--' + key, required=True)
    for name in ('worker-run', 'worker-start', 'worker-status', 'worker-stop'):
        worker = subs.add_parser(name, help='explicit managed worker lifecycle')
        worker.add_argument('--runtime', required=True)
        if name in ('worker-run', 'worker-start'):
            worker.add_argument('--interval', type=float, default=60)
            worker.add_argument('--legacy-drained', action='store_true', help='acknowledge old worker stopped and preserve unsigned policy')
        if name == 'worker-run':
            worker.add_argument('--once', action='store_true')
            worker.add_argument('--nonce', help=argparse.SUPPRESS)
            worker.add_argument('--startup-deadline', type=float, help=argparse.SUPPRESS)
        if name in ('worker-start', 'worker-stop'):
            worker.add_argument('--timeout', type=float, default=60)
    replacement = subs.add_parser('mac-replace', help='operator-confirmed Mac replacement; planning is read-only')
    replacement.add_argument('--manifest', required=True, help='private explicit existing-install manifest')
    replacement.add_argument('--binary', help='bundled standalone executable; defaults to this frozen executable')
    replacement.add_argument('--dry-run', action='store_true')
    replacement.add_argument('--timeout', type=float, default=300)
    rollback = subs.add_parser('mac-rollback', help='operator-confirmed code/service rollback; never restores SQLite')
    rollback.add_argument('--backup', required=True)
    rollback.add_argument('--timeout', type=float, default=300)
    args = parser.parse_args(argv)
    try:
        if args.action in ('mac-replace', 'mac-rollback'):
            import mac_replace
            if args.action == 'mac-rollback':
                report = mac_replace.rollback(args.backup, timeout=args.timeout)
            else:
                binary = args.binary or (sys.executable if getattr(sys, 'frozen', False) else None)
                if binary is None:
                    raise ValueError('source replacement requires an explicit bundled binary')
                report = mac_replace.replace(args.manifest, binary, timeout=args.timeout, dry_run=args.dry_run)
            print(json.dumps(report, sort_keys=True))
            if report.get('error') or report['status'] == 'recovery_required':
                return 1
            return 2 if report['status'] == 'pending' else 0
        if args.action.startswith('worker-'):
            import worker_lifecycle as managed
            if args.action == 'worker-run':
                return managed.run(args.runtime, once=args.once, interval=args.interval,
                    legacy_drained=args.legacy_drained, nonce=args.nonce, startup_deadline=args.startup_deadline)
            if args.action == 'worker-start':
                report = managed.start(args.runtime, interval=args.interval, legacy_drained=args.legacy_drained, timeout=args.timeout)
            elif args.action == 'worker-stop':
                report = managed.stop(args.runtime, timeout=args.timeout)
            else:
                report = managed.status(args.runtime)
            print(json.dumps(report, sort_keys=True))
            return 0
        if args.action == 'adopt-install':
            from install_adopt import adopt
            report = adopt(**{key: getattr(args, key) for key in ('runtime', 'app_root', 'database', 'exchange', 'node', 'security_dir', 'security_state', 'workflow_config')})
            print(json.dumps(report, sort_keys=True))
            return 0 if report['status'] == 'bound' else 2
        if args.action == 'setup-new':
            from install_setup import setup_new
            report = setup_new(args.local_dir, args.exchange, args.node)
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            return 0 if report['status'] == 'created' else 2
        if args.action in ('inspect-install', 'wizard-status', 'wizard-resume'):
            from install_setup import reject_partial
            reject_partial(args.runtime)
        if args.action == 'wizard-resume':
            from install_inspect import wizard_resume
            report = wizard_resume(args.runtime)
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            ready = (report['policy'] == 'required' and report['wizard_step'] == 'active'
                     and report['pairing'] == 'approved' and all(report['prerequisites'].values()))
            return 0 if ready else 2
        if args.action in ('inspect-install', 'wizard-status'):
            from install_inspect import inspect, wizard_status
            report = inspect(args.runtime) if args.action == 'inspect-install' else wizard_status(args.runtime)
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            return 0
        if not args.database:
            parser.error('--database is required for this action')
        db = Path(args.database).resolve()
        if not db.is_file():
            raise FileNotFoundError('database must already be initialized')
        if args.action == 'recall':
            from recall_memory import recall
            result = recall(db, args.query, project=args.project, limit=args.limit)
        elif args.action == 'ingest':
            from recall_memory import open_readonly
            with open_readonly(db) as c:
                if c.execute("SELECT 1 FROM sqlite_master WHERE name='_pg_shadow'").fetchone():
                    raise ValueError('PostgreSQL mirror owns transcript ingestion on this source machine')
            from ingest_sessions import ingest
            roots = dict(root.split('=', 1) for root in args.root) if args.root else None
            result = ingest(db, roots=roots, project=args.project)
        elif args.action == 'summarize':
            import workflow
            import summarize_memory
            if args.preview:
                result = summarize_memory.prepare_task(db)
                if result is not None:
                    result = {k: v for k, v in result.items() if not k.startswith('_')}
            else:
                settings = workflow.load(db, args.config)
                if settings is None:
                    raise ValueError('summarization requires private provider configuration')
                settings['summarize'] = True
                result = workflow.summarize(db, settings, force=True)
        elif args.action in ('once', 'watch'):
            if not args.exchange:
                raise ValueError('worker requires --exchange')
            if args.action == 'watch':
                command = [str(db), args.exchange, '--interval', str(args.interval)]
                if args.postgres:
                    command += ['--postgres']
                if args.config:
                    command += ['--workflow-config', args.config]
                return sync_worker.main(command)
            dsn = os.environ.get('OMP_MEMORY_DSN') if args.postgres else None
            if args.postgres and not dsn:
                raise ValueError('--postgres requires OMP_MEMORY_DSN')
            result = sync_worker.run_once(db, args.exchange, dsn,
                                         workflow_config=args.config, force_summary=True)
        else:
            import memory_sync
            import sqlite_memory
            result = {'sync': memory_sync.status(db), 'counts': sqlite_memory.status(db)['counts']}
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        if args.action == 'once':
            return int(sync_worker.failed(result))
        return 0
    except Exception as exc:
        from cli_errors import report as error_report
        print(json.dumps(error_report(exc, args.action)), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
