"""Install OMP Memory Sync outside its Syncthing exchange folder."""
from contextlib import closing
from pathlib import Path, PurePosixPath
import os
import stat
import zipfile


def extract_package(archive, app_dir):
    with zipfile.ZipFile(archive) as package:
        members = package.infolist()
        if sum(m.file_size for m in members) > 200_000_000:
            raise ValueError('unsafe archive size')
        seen = set()
        for member in members:
            name = member.filename
            p = PurePosixPath(name)
            if (not name or p.is_absolute() or '..' in p.parts or '\\' in name
                    or ':' in name or stat.S_ISLNK(member.external_attr >> 16)
                    or name.casefold() in seen):
                raise ValueError('unsafe archive member')
            seen.add(name.casefold())
        destination = Path(app_dir)
        destination.mkdir(parents=True, exist_ok=True)
        for member in members:
            target = destination / member.filename
            if any(part.is_symlink() for part in [target, *target.parents]):
                raise ValueError('unsafe destination symlink')
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.open(member) as source, target.open('wb') as out:
                    import shutil
                    shutil.copyfileobj(source, out)
    return destination


def prepare_baseline(app_dir, data_dir):
    import gzip
    import hashlib
    import json
    import tempfile
    app = Path(app_dir)
    data = Path(data_dir)
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    expected = json.loads((app / 'bootstrap-manifest.json').read_text(encoding='utf-8'))['snapshot_sha256']
    fd, temporary = tempfile.mkstemp(prefix='baseline-', suffix='.partial', dir=data)
    temporary = Path(temporary)
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(fd, 'wb') as out, gzip.open(app / 'baseline.jsonl.gz', 'rb') as source:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > 200_000_000:
                    raise ValueError('unsafe baseline size')
                digest.update(chunk)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        if digest.hexdigest() != expected:
            raise ValueError('baseline checksum mismatch')
        result = data / 'baseline.jsonl'
        os.replace(temporary, result)
        return result
    finally:
        temporary.unlink(missing_ok=True)


def install(exchange, local_dir, node='windows', progress=None):
    import hashlib
    import json
    import sqlite3
    import sys
    import uuid
    progress = progress or (lambda message: None)
    progress('[1/6] Verifying package checksum')
    exchange = Path(exchange).expanduser().resolve(strict=True)
    local = Path(local_dir).expanduser().resolve()
    if local == exchange or exchange in local.parents or local in exchange.parents:
        raise ValueError('local application and database must be outside the exchange folder')
    if not (exchange / '.stfolder').exists():
        raise ValueError('exchange is not an accepted Syncthing folder')
    if node not in ('mac', 'windows', 'linux'):
        raise ValueError('invalid node identity')
    archive = exchange / 'AgentMesh-bootstrap-v1.zip'
    expected = json.loads((exchange / 'agentmesh-package.json').read_text(encoding='utf-8'))['archive_sha256']
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
        raise ValueError('package checksum mismatch; wait for Syncthing to finish')
    progress('[2/6] Extracting application files')
    app = extract_package(archive, local / 'app')
    manifest = json.loads((app / 'bootstrap-manifest.json').read_text(encoding='utf-8'))
    sys.path.insert(0, str(app))
    import sqlite_memory
    import memory_sync
    data = local / 'data'
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = data / (node + '.db')
    if not db.exists():
        progress('[3/6] Decompressing and verifying memory snapshot')
        baseline = prepare_baseline(app, data)
        staging = data / ('install-' + str(uuid.uuid4()) + '.db')
        ready = data / ('ready-' + str(uuid.uuid4()) + '.db')
        try:
            total = sum(manifest['table_counts'].values())
            progress(f'[4/6] Importing {total:,} rows into SQLite')
            counts = sqlite_memory.import_snapshot(staging, baseline,
                progress=lambda done: progress(f'[4/6] Imported {done:,}/{total:,} rows'))
            if counts != manifest['table_counts']:
                raise ValueError('bootstrap table counts mismatch')
            progress('[5/6] Initializing sync revisions and ID allocation')
            memory_sync.initialize(staging, node, manifest['group_id'])
            progress('[6/6] Saving the verified local database')
            # Connection context managers commit/rollback but do not close.
            # Windows requires both handles closed before rename or cleanup.
            with closing(sqlite3.connect(staging)) as src, closing(sqlite3.connect(ready)) as dst:
                src.backup(dst)
            os.chmod(ready, 0o600)
            if db.exists():
                raise ValueError('another installer created the database; rerun safely')
            os.replace(ready, db)
        finally:
            for p in [staging, ready, Path(str(staging)+'-wal'), Path(str(staging)+'-shm')]:
                p.unlink(missing_ok=True)
    progress('[6/6] Checking local database identity')
    state = memory_sync.status(db)
    if state['node'] != node or state['group_id'] != manifest['group_id']:
        raise ValueError('existing database node/group differs; refusing to replace it')
    workflow_path = data / 'workflow.json'
    if not workflow_path.exists():
        try:
            fd = os.open(workflow_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as out:
                json.dump({'ingest': True, 'summarize': False,
                           'summary_interval_seconds': 86400}, out)
                out.write('\n')
        except FileExistsError:
            pass
    result = {'database': str(db), 'exchange': str(exchange), 'app': str(app), **state}
    (data / 'runtime.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    progress('Ready: local database installed; starting synchronization')
    return result

class ConsoleProgress:
    """Display stage and elapsed time, not a fabricated completion percentage."""
    def __init__(self, interval=5):
        import threading
        import time
        self.interval = interval
        self.started = time.monotonic()
        self.stage = 'Starting'
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._heartbeat, daemon=True)

    def __call__(self, message):
        self.stage = message
        print(message, flush=True)

    def _heartbeat(self):
        import time
        while not self.stop.wait(self.interval):
            print(f'  {self.stage} | elapsed {time.monotonic() - self.started:.0f}s', flush=True)

    def __enter__(self):
        print('AgentMesh bootstrap starting - checking the private installation', flush=True)
        try:
            print(__import__('brand').banner(), flush=True)
        except ImportError:
            print('[o-A-o] AgentMesh', flush=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join()


def main(argv=None):
    import argparse
    import json
    import sys
    parser = argparse.ArgumentParser(description=__import__('brand').description(__doc__),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--exchange', default=str(Path(__file__).resolve().parent))
    parser.add_argument('--node', choices=('mac', 'windows', 'linux'), default='windows')
    parser.add_argument('--local-dir', default=str(Path(os.environ.get('LOCALAPPDATA', str(Path.home() / '.local/share'))) / 'AgentMesh'))
    parser.add_argument('--once', action='store_true', help='install and sync once, without keeping a watcher open')
    args = parser.parse_args(argv)
    try:
        with ConsoleProgress() as progress:
            result = install(args.exchange, args.local_dir, node=args.node, progress=progress)
        print(json.dumps({'installed': True, **result}), flush=True)
        import sync_worker
        if args.once:
            print(json.dumps(sync_worker.run_once(result['database'], result['exchange'])), flush=True)
            return 0
        print('Keep this window open for sync. Ctrl-C stops the worker; Syncthing is separate.', flush=True)
        return sync_worker.main([result['database'], result['exchange'], '--interval', '60'])
    except Exception as exc:
        print(json.dumps({'error': type(exc).__name__, 'detail': str(exc)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
