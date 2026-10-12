import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile


def test_build_package_preserves_snapshot_and_publishes_only_allowlisted_sources(tmp_path):
    root = Path(__file__).parent
    spec = importlib.util.spec_from_file_location('package_builder', root / 'build_package.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    tables = ['source_sessions', 'observation_events', 'ingestion_cursors', 'ingestion_errors', 'memory_items', 'memory_sources', 'memory_summaries', 'summary_state']
    snapshot = tmp_path / 'baseline.jsonl'
    content = json.dumps({'format': 'omp-sqlite-snapshot-v1', 'tables': tables}) + '\n'
    snapshot.write_text(content)
    exchange = tmp_path / 'exchange'
    exchange.mkdir()
    (exchange / '.stfolder').mkdir()
    result = builder.build(snapshot, exchange, '00000000-0000-4000-8000-000000000001')
    # The launcher needs the ACL helper before any archive payload is extracted.
    assert (exchange / 'windows_acl.py').read_bytes() == (root / 'windows_acl.py').read_bytes()
    archive = exchange / result['archive']
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == result['archive_sha256']
    with zipfile.ZipFile(archive) as package:
        assert gzip.decompress(package.read('baseline.jsonl.gz')).decode() == content
        names = package.namelist()
        assert all(not name.startswith('/') and '..' not in Path(name).parts for name in names)
        assert not any(name.endswith(('.db', '.env')) or 'deployment' in name for name in names)
        assert 'memory_sync.py' in names
        assert {'bounded_digest.py','bounded_protocol.py','digest_worker.py','digest_scheduler.py',
                'shared_memory_context.py','sqlite_export.py','retention_audit.py','SQLITE-CONSUMERS.md'} <= set(names)
        manifest = json.loads(package.read('bootstrap-manifest.json'))
        assert manifest['table_counts'] == dict.fromkeys(tables, 0)
    assert json.loads((exchange / 'agentmesh-package.json').read_text()) == result


def test_every_local_module_imported_by_packaged_sources_is_packaged():
    import ast
    root = Path(__file__).parent
    spec = importlib.util.spec_from_file_location('package_builder', root / 'build_package.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    packaged = {name for name in builder.FILES if name.endswith('.py')}

    def imports(path):
        names = set()
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, ast.Import):
                names.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and not node.level:
                names.add(node.module.split('.')[0])
            elif (isinstance(node, ast.Call) and getattr(node.func, 'id', None) == '__import__'
                    and node.args and isinstance(node.args[0], ast.Constant)):
                names.add(node.args[0].value.split('.')[0])
        return {name + '.py' for name in names if (root / (name + '.py')).is_file()}

    pending, seen = list(packaged), set()
    while pending:
        name = pending.pop()
        if name not in seen:
            seen.add(name)
            pending.extend(imports(root / name))
    assert sorted(seen - packaged) == []
