"""Build private peer bootstrap artifacts; never publish these artifacts to Git."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid
import zipfile

FILES = ['bounded_digest.py', 'bounded_protocol.py', 'digest_worker.py', 'digest_scheduler.py',
         'shared_memory_context.py', 'sqlite_export.py', 'retention_audit.py', 'SQLITE-CONSUMERS.md', 'SQLITE-WRITERS.md',
         'brand.py', 'terminal_progress.py', 'agentmesh.py', 'workflow.py', 'ingest_sessions.py',
         'recall_memory.py', 'summarize_memory.py', 'claude_summary_provider.py',
         'install_agent_rules.py', 'WORKFLOW.md',
         'assets/agentmesh-icon.svg', 'assets/agentmesh-icon-mono.svg',
         'assets/agentmesh-logo.svg', 'assets/agentmesh-logo-dark.svg', 'assets/PROVENANCE.md',
         'sqlite_memory.py', 'memory_sync.py', 'signed_packets.py', 'security_wizard.py',
         'SIGNED-SYNC.md', 'SECURITY-WIZARD.md',
         'requirements-security.txt', 'pg_mirror.py', 'sync_worker.py',
         'schema.sql', 'bootstrap_windows.py', 'platform_service.py',
         'START-WINDOWS.cmd', 'README-WINDOWS.md', 'README-SETUP.md',
         'setup_macos.sh', 'setup_linux.sh', 'SYNC.md']
TABLES = ['source_sessions', 'observation_events', 'ingestion_cursors',
          'ingestion_errors', 'memory_items', 'memory_sources',
          'memory_summaries', 'summary_state']


def build(snapshot, exchange, group_id):
    if str(uuid.UUID(group_id)) != group_id:
        raise ValueError('group must be a canonical UUID')
    snapshot, exchange = Path(snapshot).resolve(), Path(exchange).resolve()
    if not exchange.is_dir() or not (exchange / '.stfolder').is_dir():
        raise ValueError('exchange must be an existing Syncthing folder')
    counts = dict.fromkeys(TABLES, 0)
    digest = hashlib.sha256()
    with snapshot.open('rb') as stream:
        first = stream.readline()
        header = json.loads(first)
        if header.get('format') != 'omp-sqlite-snapshot-v1' or set(header['tables']) != set(TABLES):
            raise ValueError('unsupported snapshot')
        digest.update(first)
        for line in stream:
            digest.update(line)
            if line.strip():
                entry = json.loads(line)
                counts[entry['table']] += 1
    manifest = {'group_id': group_id, 'snapshot_sha256': digest.hexdigest(), 'table_counts': counts}
    root = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix='.agentmesh-build-', dir=exchange) as temp:
        temp = Path(temp)
        compressed = temp / 'baseline.jsonl.gz'
        with snapshot.open('rb') as source, gzip.open(compressed, 'wb') as target:
            import shutil
            shutil.copyfileobj(source, target)
        archive = temp / 'AgentMesh-bootstrap-v1.zip'
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as package:
            for name in FILES:
                package.write(root / name, name)
            for path in sorted((root / 'src').rglob('*.py')):
                if path.is_symlink() or not path.resolve().is_relative_to(root / 'src'):
                    raise ValueError('source symlink not permitted')
                package.write(path, str(path.relative_to(root)))
            package.write(compressed, 'baseline.jsonl.gz')
            package.writestr('bootstrap-manifest.json', json.dumps(manifest, sort_keys=True))
        result = {'format': 'agentmesh-bootstrap-package-v1', 'archive': archive.name,
                  'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                  'table_counts': counts}
        # Bootstrap validates the archive hash before touching its database.
        os.replace(archive, exchange / archive.name)
        for name in ['bootstrap_windows.py', 'START-WINDOWS.cmd', 'README-WINDOWS.md',
                     'README-SETUP.md', 'setup_macos.sh', 'setup_linux.sh', 'platform_service.py', 'brand.py', 'terminal_progress.py',
                     'assets/agentmesh-icon.svg', 'assets/agentmesh-icon-mono.svg',
                     'assets/agentmesh-logo.svg', 'assets/agentmesh-logo-dark.svg', 'assets/PROVENANCE.md']:
            staged = temp / name
            staged.parent.mkdir(parents=True, exist_ok=True)
            (exchange / name).parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes((root / name).read_bytes())
            os.replace(staged, exchange / name)
        metadata = temp / 'agentmesh-package.json'
        metadata.write_text(json.dumps(result, sort_keys=True) + '\n', encoding='utf-8')
        os.replace(metadata, exchange / metadata.name)
    return result


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__import__('brand').description(__doc__),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--snapshot', required=True)
    parser.add_argument('--exchange', required=True)
    parser.add_argument('--group', required=True)
    args = parser.parse_args(argv)
    print(json.dumps(build(args.snapshot, args.exchange, args.group)))


if __name__ == '__main__':
    main()
