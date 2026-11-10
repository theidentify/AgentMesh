"""`python -m agentmesh_log.ingest scan|summary [--config PATH] [--force]`: the scheduled jobs, standard library only.

The supervisor runs this form (see control/rewire.py), so a job interpreter without the broker's dependencies still
works, as memory-sync's jobs did. `agentmesh-log ingest scan|summary` is the same job through the full CLI.
"""
import argparse
import sys

from .production import execute


def main(argv=None):
    p = argparse.ArgumentParser(prog='python -m agentmesh_log.ingest')
    p.add_argument('action', choices=['scan', 'summary'])
    p.add_argument('--config')
    p.add_argument('--force', action='store_true')
    args = p.parse_args(argv)
    if args.force and args.action != 'summary':
        p.error('--force applies to summary only')
    return execute('ingest' if args.action == 'scan' else 'summary', config=args.config, force=args.force)


if __name__ == '__main__':
    sys.exit(main())
