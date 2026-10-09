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
    args = parser.parse_args(argv)
    try:
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
        print(json.dumps({'error': type(exc).__name__}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
