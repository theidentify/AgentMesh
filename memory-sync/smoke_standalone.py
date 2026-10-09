"""Smoke-test a locally built standalone CLI on a disposable installation.

Run after standalone_build.py on each native OS. Never use a production DB.
"""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile

import memory_sync
import sqlite_memory


def main():
    dist = Path(os.environ.get('AGENTMESH_DIST', str(Path(os.environ['RUNNER_TEMP']) / 'agentmesh-dist')))
    binary = dist / ('agentmesh.exe' if os.name == 'nt' else 'agentmesh')
    assert binary.is_file(), f'missing standalone binary: {binary}'
    with tempfile.TemporaryDirectory(prefix='agentmesh-native-smoke-', dir=os.environ['RUNNER_TEMP']) as scratch:
        root = Path(scratch)
        db = root / 'data' / 'mac.db'
        db.parent.mkdir()
        sqlite_memory.init_database(db)
        memory_sync.initialize(db, 'mac', '00000000-0000-4000-8000-000000000001')
        exchange = root / 'exchange'
        (exchange / '.stfolder').mkdir(parents=True)
        runtime = root / 'runtime.json'
        runtime.write_text(json.dumps({'database': str(db), 'exchange': str(exchange), 'node': 'mac'}))
        source_before = db.read_bytes(), runtime.read_bytes()
        def run(*args):
            command = [str(binary), *map(str, args)]
            result = subprocess.run(command, cwd=root, text=True, capture_output=True, timeout=90)
            assert result.returncode == 0, (command, result.stderr)
            return result.stdout
        assert 'inspect-install' in run('--help')
        assert json.loads(run('--database', db, 'status'))['sync']['node'] == 'mac'
        assert json.loads(run('--database', db, 'recall', 'example'))['results'] == []
        assert json.loads(run('inspect-install', '--runtime', runtime))['status'] == 'ready'
        assert (db.read_bytes(), runtime.read_bytes()) == source_before
        print('native standalone smoke: help/status/recall/inspect-install passed')


if __name__ == '__main__':
    main()
