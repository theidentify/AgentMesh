"""Install OMP Memory Sync outside its Syncthing exchange folder."""
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
