"""Compatibility shim (memory-sync retirement): the jobs are now `python -m agentmesh_log.ingest scan|summary`.

Kept for jobs/agentmesh_ingest.py and agentmesh_summary.py, here or copied into ~/.agentmesh/jobs, until every device
runs `control rewire-ingest`; PR B removes it. agentmesh_log comes from the installed package, else from profile-log/
beside the config's `runtime` (the memory-sync directory of a deployed checkout)."""
import importlib
import json
import os
import sys
from pathlib import Path

_LOCAL = Path(__file__).resolve().with_name('agentmesh-production.json')
_HOME = Path(os.environ.get('AGENTMESH_HOME', str(Path.home() / '.agentmesh')))
CONFIG = _LOCAL if _LOCAL.is_file() else _HOME / 'jobs/agentmesh-production.json'


def _production():
    try:
        import agentmesh_log  # noqa: F401
    except ImportError:
        try:
            runtime = json.loads(CONFIG.read_text(encoding='utf-8')).get('runtime')
        except (OSError, ValueError):
            runtime = None
        for base in ([Path(runtime)] if isinstance(runtime, str) else []) + [Path(__file__).resolve().parent.parent]:
            if (base.parent / 'profile-log' / 'agentmesh_log').is_dir():
                sys.path.insert(0, str(base.parent / 'profile-log'))
                break
    return importlib.import_module('agentmesh_log.ingest.production')


def execute(action, argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    return _production().execute(action, config=CONFIG, force='--force' in args)
