import importlib.util
from pathlib import Path
import zipfile

import pytest


def load_bootstrap():
    path = Path(__file__).with_name('bootstrap_windows.py')
    assert path.exists(), 'Windows bootstrap implementation missing'
    spec = importlib.util.spec_from_file_location('omp_windows_bootstrap', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_extract_rejects_traversal_without_writing_files(tmp_path):
    archive = tmp_path / 'package.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('good.py', 'print(1)')
        z.writestr('../escaped.py', 'bad')
    with pytest.raises(ValueError, match='unsafe'):
        load_bootstrap().extract_package(archive, tmp_path / 'app')
    assert not (tmp_path / 'escaped.py').exists()
    assert not (tmp_path / 'app/good.py').exists()


def test_prepare_baseline_verifies_hash_and_keeps_data_outside_exchange(tmp_path):
    import gzip
    import hashlib
    import json
    app = tmp_path / 'app'
    app.mkdir()
    baseline = b'{"format":"omp-sqlite-snapshot-v1","tables":[]}\n'
    (app / 'bootstrap-manifest.json').write_text(json.dumps({'snapshot_sha256': hashlib.sha256(baseline).hexdigest()}))
    (app / 'baseline.jsonl.gz').write_bytes(gzip.compress(baseline))
    data = tmp_path / 'private-data'
    m = load_bootstrap()
    result = m.prepare_baseline(app, data)
    assert result.read_bytes() == baseline
    (app / 'baseline.jsonl.gz').write_bytes(gzip.compress(b'corrupt'))
    with pytest.raises(ValueError, match='checksum'):
        m.prepare_baseline(app, tmp_path / 'second-data')
    assert not (tmp_path / 'second-data/baseline.jsonl').exists()


def test_portable_ingest_works_without_installed_project(tmp_path):
    import json
    import subprocess
    import sys
    root = Path(__file__).parent
    db = tmp_path / 'local.db'
    transcript = tmp_path / 'session.jsonl'
    transcript.write_text(json.dumps({'type': 'message', 'id': 'one', 'message': {'role': 'user', 'content': [{'type': 'text', 'text': 'portable ingestion'}]}}) + '\n')
    base = [sys.executable, '-S', str(root / 'sqlite_memory.py')]
    first = subprocess.run(base + ['init', str(db)], capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    ingested = subprocess.run(base + ['ingest', str(db), str(transcript)], capture_output=True, text=True)
    assert ingested.returncode == 0, ingested.stderr
    assert json.loads(ingested.stdout)['inserted'] == 1
