#!/usr/bin/env python3
# Legacy Hermes-era name kept for compatibility; launchd runs agentmesh_ingest.py.
from agentmesh_production import execute
if __name__ == '__main__':
    raise SystemExit(execute('ingest'))
