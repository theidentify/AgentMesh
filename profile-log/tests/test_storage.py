import builtins
import os
from pathlib import Path
import subprocess
import sys
import pytest
from moto import mock_aws
from agentmesh_log.storage import InvalidKey, ObjectNotFound, ObjectExists, StorageError
from agentmesh_log.storage.filesystem import FilesystemStorage
from agentmesh_log.storage.s3 import S3Storage


@pytest.fixture(params=['filesystem', 's3'], ids=['fs', 's3'])
def backend(request, tmp_path):
    if request.param == 'filesystem':
        yield FilesystemStorage(tmp_path / 'store')
    else:
        import boto3
        from botocore.config import Config
        with mock_aws():
            client = boto3.client('s3', region_name='us-east-1', aws_access_key_id='testing',
                                  aws_secret_access_key='testing', config=Config(retries={'max_attempts': 0}))
            client.create_bucket(Bucket='profile-log-tests')
            yield S3Storage('profile-log-tests', client=client)


class TestContract:
    def test_roundtrip_and_missing(self, backend):
        backend.put('events/a/01.json', b'\x00opaque\xff')
        assert backend.get('events/a/01.json') == b'\x00opaque\xff'
        with pytest.raises(ObjectNotFound): backend.get('absent')

    def test_sorted_prefixes(self, backend):
        keys = ['events/b/z.json', 'events/a/nested/b.json', 'events/a/a.json', 'keys/a/key.json']
        for key in keys: backend.put(key, key.encode())
        assert list(backend.list('')) == sorted(keys)
        assert list(backend.list('events/a/')) == sorted(keys[1:3])
        assert list(backend.list('events/a/a')) == ['events/a/a.json']
        assert list(backend.list('no-match')) == []

    @pytest.mark.parametrize('key', ['', '/', '../a', 'a/../b', '/abs', 'a\\b', 'a//b',
        './a', 'a/./b', '.hidden', 'a/.hidden', 'a/', 'a b', 'é', 'a'*1025, None, 1],
        ids=['empty', 'slash', 'up', 'traverse', 'abs', 'back', 'double', 'dot', 'dotseg', 'hidden', 'nested', 'trail', 'space', 'unicode', 'long', 'null', 'type'])
    def test_key_before_io(self, backend, monkeypatch, key):
        def forbidden(*args, **kwargs): pytest.fail('I/O attempted for invalid key')
        if isinstance(backend, S3Storage):
            for name in ('put_object', 'get_object', 'get_paginator'):
                monkeypatch.setattr(backend.client, name, forbidden)
        else:
            monkeypatch.setattr(os, 'open', forbidden)
            monkeypatch.setattr(os, 'walk', forbidden)
            monkeypatch.setattr(Path, 'lstat', forbidden)
        with pytest.raises(InvalidKey): backend.put(key, b'data')
        with pytest.raises(InvalidKey): backend.get(key)
        if key not in ('', 'a/'):
            with pytest.raises(InvalidKey): list(backend.list(key))


def test_filesystem_immutable_and_private(tmp_path):
    store = FilesystemStorage(tmp_path / 'root')
    store.put('a/b', b'original'); store.put('a/b', b'original')
    with pytest.raises(ObjectExists): store.put('a/b', b'changed')
    assert store.get('a/b') == b'original'
    if os.name != 'nt': assert (tmp_path / 'root/a/b').stat().st_mode & 0o077 == 0
    assert list((tmp_path / 'root/a').iterdir()) == [tmp_path / 'root/a/b']


def make_symlink(source, target, directory=False):
    try: source.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError): pytest.skip('symlinks unavailable on this host')


def test_filesystem_symlinks(tmp_path):
    outside = tmp_path / 'outside'; outside.mkdir(); (outside / 'secret').write_bytes(b'private')
    root_link = tmp_path / 'root-link'; make_symlink(root_link, outside, True)
    with pytest.raises(StorageError): FilesystemStorage(root_link)
    store = FilesystemStorage(tmp_path / 'root')
    make_symlink(tmp_path / 'root/sub', outside, True)
    for op in (lambda: store.get('sub/secret'), lambda: store.put('sub/new', b'new'), lambda: list(store.list(''))):
        with pytest.raises(StorageError): op()
    assert not (outside / 'new').exists()
    (tmp_path / 'root/sub').unlink()
    make_symlink(tmp_path / 'root/file', outside / 'secret')
    with pytest.raises(StorageError): store.get('file')
    with pytest.raises(StorageError): store.put('file', b'private')


def test_filesystem_dot_and_temp_files(tmp_path):
    store = FilesystemStorage(tmp_path / 'root'); store.put('a/b', b'data')
    (tmp_path / 'root/.stfolder').mkdir()
    (tmp_path / 'root/.stfolder/marker').write_text('marker')
    (tmp_path / 'root/a/.pending.tmp').write_text('pending')
    assert list(store.list('')) == ['a/b']


def test_s3_pagination_and_isolation():
    import boto3
    with mock_aws():
        client = boto3.client('s3', region_name='us-east-1', aws_access_key_id='testing', aws_secret_access_key='testing')
        client.create_bucket(Bucket='profile-pagination')
        first = S3Storage('profile-pagination', prefix='one/', client=client)
        second = S3Storage('profile-pagination', prefix='two', client=client)
        for index in range(7): first.put(f'events/{index}', b'data')
        second.put('events/private', b'other')
        original = client.get_paginator
        pages = []
        class SmallPages:
            def paginate(self, **kwargs):
                for page in original('list_objects_v2').paginate(**kwargs, PaginationConfig={'PageSize': 2}):
                    pages.append(page); yield page
        client.get_paginator = lambda name: SmallPages()
        assert list(first.list('events/')) == [f'events/{i}' for i in range(7)]
        assert len(pages) == 4
        assert list(second.list('')) == ['events/private']
        with pytest.raises(ObjectNotFound): second.get('events/0')


def test_s3_custom_http_endpoint():
    import boto3
    from botocore.config import Config
    from moto.server import ThreadedMotoServer
    # Moto.start waits forever if its background bind fails. Fail before starting it.
    import socket
    with socket.socket() as probe:
        try: probe.bind(('127.0.0.1', 0))
        except PermissionError:
            pytest.fail('HTTP acceptance gate blocked: sandbox denies 127.0.0.1 bind', pytrace=False)
    server = ThreadedMotoServer(ip_address='127.0.0.1', port=0, verbose=False)
    server.start()
    try:
        host, port = server.get_host_and_port()
        endpoint = f'http://{host}:{port}'
        calls = []
        def factory(service, **kwargs):
            calls.append(kwargs)
            return boto3.client(service, **kwargs, aws_access_key_id='testing', aws_secret_access_key='testing',
                config=Config(s3={'addressing_style': 'path'}, proxies={}, retries={'max_attempts': 0}))
        store = S3Storage('profile-endpoint', endpoint_url=endpoint, region_name='us-east-1', client_factory=factory)
        store.client.create_bucket(Bucket='profile-endpoint')
        store.put('events/a', b'opaque')
        assert store.get('events/a') == b'opaque'
        assert list(store.list('events/')) == ['events/a']
        assert calls[0]['endpoint_url'] == endpoint
    finally:
        server.stop()


def test_optional_boto3_import(tmp_path):
    code = '''
import builtins
original = builtins.__import__
def blocked(name, *args, **kwargs):
    if name == 'boto3' or name.startswith('boto3.'):
        raise ImportError('simulated missing dependency')
    return original(name, *args, **kwargs)
builtins.__import__ = blocked
from agentmesh_log.storage.filesystem import FilesystemStorage
from agentmesh_log.storage.s3 import S3Storage
from agentmesh_log.storage import StorageError
store = FilesystemStorage(ROOT)
store.put('a', b'data')
assert store.get('a') == b'data'
try:
    S3Storage('bucket')
except StorageError as exc:
    assert 'boto3' in str(exc)
else:
    raise AssertionError('missing boto3 not reported')
'''
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    result = subprocess.run([sys.executable, '-c', 'ROOT = ' + repr(str(tmp_path / 'isolated')) + '\n' + code],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('stray', ['notes bar.json', 'mémoire.json', 'stray folder/item.json'],
    ids=['space', 'unicode', 'dir'])
def test_list_skips_invalid_names(backend, stray):
    backend.put('events/good.json', b'good')
    backend.put('events/z.json', b'last')
    key = 'events/' + stray
    if isinstance(backend, FilesystemStorage):
        path = backend.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'stray')
    else:
        backend.client.put_object(Bucket=backend.bucket, Key=backend.prefix + key, Body=b'stray')
    assert list(backend.list('events/')) == ['events/good.json', 'events/z.json']
    assert list(backend.list('')) == ['events/good.json', 'events/z.json']


@pytest.mark.parametrize('kind', ['symlink', 'fifo'],
    ids=['link', 'fifo'])
def test_invalid_name_unsafe_file(tmp_path, kind):
    store = FilesystemStorage(tmp_path / 'store')
    path = store.root / 'stray name'
    if kind == 'symlink':
        target = tmp_path / 'target'; target.write_bytes(b'private')
        make_symlink(path, target)
    else:
        if not hasattr(os, 'mkfifo'): pytest.skip('FIFO unavailable')
        os.mkfifo(path)
    with pytest.raises(StorageError): list(store.list(''))


@pytest.mark.parametrize('prefix,start,expected', [
    ('events/profile/scope/', 'events/profile/scope', ['events/profile/scope/a.json']),
    ('events/pr', 'events', ['events/profile/scope/a.json']),
    ('events/missing/scope/', None, []),
    ('events/profile/scope/a.json/', None, []),
],
    ids=['full', 'partial', 'missing', 'file'])
def test_filesystem_prefix_limits_walk(tmp_path, monkeypatch, prefix, start, expected):
    store = FilesystemStorage(tmp_path / 'store')
    for key in ('events/profile/scope/a.json', 'events/other/scope/b.json', 'keys/unrelated/c.json'):
        store.put(key, b'data')
    walks, visited = [], []
    original_walk, original_scandir = os.walk, os.scandir
    def walk(path, **kwargs):
        walks.append(Path(path).relative_to(store.root).as_posix())
        return original_walk(path, **kwargs)
    def scandir(path):
        visited.append(Path(path).relative_to(store.root).as_posix())
        return original_scandir(path)
    monkeypatch.setattr(os, 'walk', walk)
    monkeypatch.setattr(os, 'scandir', scandir)
    assert list(store.list(prefix)) == expected
    assert walks == ([] if start is None else [start])
    assert all(v == start or v.startswith(start + '/') for v in visited)


def test_filesystem_prefix_checks_each_ancestor(tmp_path):
    store = FilesystemStorage(tmp_path / 'store')
    outside = tmp_path / 'outside'; (outside / 'scope').mkdir(parents=True)
    (outside / 'scope/a.json').write_bytes(b'private')
    make_symlink(store.root / 'events', outside, True)
    with pytest.raises(StorageError): list(store.list('events/scope/'))
    with pytest.raises(StorageError): list(store.list('events/missing/'))


@pytest.mark.skipif(os.name != 'nt', reason='Windows directory junctions only')
@pytest.mark.parametrize('location', ['root', 'child'], ids=['root', 'child'])
def test_windows_junction_refusal(tmp_path, location):
    import _winapi
    import stat

    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'secret').write_bytes(b'private')
    store = FilesystemStorage(tmp_path / 'store')
    if location == 'root':
        junction = store.root
        junction.rmdir()
        prefix = ''
    else:
        junction = store.root / 'sub'
        prefix = 'sub/'
    # Junctions do not require the symlink privilege or Developer Mode.
    _winapi.CreateJunction(str(outside), str(junction))
    try:
        assert junction.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
        if location == 'root':
            with pytest.raises(StorageError): FilesystemStorage(junction)
        with pytest.raises(StorageError): store.put(prefix + 'new', b'new')
        with pytest.raises(StorageError): store.get(prefix + 'secret')
        with pytest.raises(StorageError): list(store.list(''))
        with pytest.raises(StorageError): list(store.list(prefix))
        assert (outside / 'secret').read_bytes() == b'private'
        assert not (outside / 'new').exists()
    finally:
        # Remove the junction itself, never recursively delete its target.
        os.rmdir(junction)
