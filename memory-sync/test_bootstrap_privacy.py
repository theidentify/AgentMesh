"""Extraction must protect payload bytes before writing and repair old installs."""
import os
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest
from test_bootstrap import load_bootstrap


@pytest.mark.skipif(os.name == 'nt', reason='POSIX modes')
@pytest.mark.parametrize('rerun', [False, True])
def test_private_extraction_and_repair(tmp_path, monkeypatch, rerun):
    archive, app = tmp_path / 'package.zip', tmp_path / 'app'
    names = ['baseline.jsonl.gz', 'bootstrap-manifest.json', 'nested/manifest.json', 'code.py']
    with zipfile.ZipFile(archive, 'w') as z:
        for name in names:
            z.writestr(name, b'private payload')
    if rerun:
        for name in names:
            target = app / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'old payload')
            target.chmod(0o644)
    original = __import__('shutil').copyfileobj
    def checked_copy(source, out, *args, **kwargs):
        assert os.fstat(out.fileno()).st_mode & 0o777 == 0o600
        return original(source, out, *args, **kwargs)
    monkeypatch.setattr('shutil.copyfileobj', checked_copy)
    previous = os.umask(0o022)
    try:
        load_bootstrap().extract_package(archive, app)
    finally:
        os.umask(previous)
    for name in names:
        assert (app / name).stat().st_mode & 0o777 == 0o600
        assert (app / name).read_bytes() == b'private payload'


@pytest.mark.parametrize('acl_failure', [False, True])
def test_windows_extraction_provisions_empty_replacement_before_payload(tmp_path, monkeypatch, acl_failure):
    import windows_acl
    module = load_bootstrap()
    # Simulate the Windows branch without changing pathlib's host platform.
    monkeypatch.setattr(module, 'os', SimpleNamespace(**{**vars(os), 'name': 'nt'}))
    archive, app = tmp_path / 'package.zip', tmp_path / 'app'
    app.mkdir()
    target = app / 'baseline.jsonl.gz'
    target.write_bytes(b'old payload')
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr(target.name, b'new private payload')
    provisioned = []
    def apply(path, *, provision=False):
        assert provision
        assert Path(path).read_bytes() == b''
        provisioned.append(Path(path))
        if acl_failure:
            raise ValueError('ACL failed')
    monkeypatch.setattr(windows_acl, 'apply', apply)
    if acl_failure:
        with pytest.raises(ValueError, match='ACL failed'):
            module.extract_package(archive, app)
        assert target.read_bytes() == b'old payload'
    else:
        module.extract_package(archive, app)
        assert target.read_bytes() == b'new private payload'
    assert len(provisioned) == 1
    assert list(app.iterdir()) == [target]


@pytest.mark.skipif(os.name != 'nt', reason='requires native Windows ACLs')
def test_native_windows_extraction_repairs_acl(tmp_path):
    import windows_acl
    archive, app = tmp_path / 'package.zip', tmp_path / 'app'
    app.mkdir()
    target = app / 'baseline.jsonl.gz'
    target.write_bytes(b'old inherited permissions')
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr(target.name, b'private payload')
    for _ in range(2):
        load_bootstrap().extract_package(archive, app)
        windows_acl.apply(target)
        assert target.read_bytes() == b'private payload'
