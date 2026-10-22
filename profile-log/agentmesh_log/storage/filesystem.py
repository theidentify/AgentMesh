"""Atomic filesystem storage; only device heartbeat slots allow replacement."""
from contextlib import contextmanager
import os
import re
from pathlib import Path
import stat
import uuid
from . import validate_key, validate_data, InvalidKey, StorageError, ObjectExists, ObjectNotFound

# Directory descriptors avoid traversing swapped symlinks on supporting platforms.
_DIR_FD = all(fn in os.supports_dir_fd for fn in (os.open, os.mkdir, os.link, os.unlink)) and hasattr(os, 'O_NOFOLLOW')


def _check(path):
    try: info = path.lstat()
    except FileNotFoundError: return
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
        raise StorageError('symlink or reparse path refused')


class FilesystemStorage:
    def __init__(self, root):
        self.root = Path(os.path.abspath(root))
        _check(self.root)
        try: self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError: raise StorageError('cannot create storage root') from None
        _check(self.root)

    @contextmanager
    def _parent(self, key, *, create=False):
        """Yield a parent descriptor on POSIX, or a checked path on other hosts."""
        _check(self.root)
        fd = None
        try:
            if _DIR_FD:
                fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                for part in key.split('/')[:-1]:
                    if create:
                        try: os.mkdir(part, mode=0o700, dir_fd=fd)
                        except FileExistsError: pass
                    next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    os.close(fd); fd = next_fd
                yield fd
            else:
                path = self.root
                for part in key.split('/')[:-1]:
                    path = path / part
                    _check(path)
                    if create: path.mkdir(mode=0o700, exist_ok=True)
                    _check(path)
                _check(path / key.split('/')[-1])
                yield path
        except FileNotFoundError:
            raise ObjectNotFound('object not found') from None
        except OSError:
            raise StorageError('filesystem operation failed') from None
        finally:
            if fd is not None: os.close(fd)

    @staticmethod
    def _open(parent, name, flags, mode=0o600):
        if isinstance(parent, int): return os.open(name, flags, mode, dir_fd=parent)
        _check(parent / name)
        return os.open(parent / name, flags, mode)

    def _read(self, parent, name):
        fd = self._open(parent, name, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode): raise StorageError('regular object file required')
            return stream.read()

    def get(self, key):
        validate_key(key)
        with self._parent(key) as parent:
            return self._read(parent, key.split('/')[-1])

    def put(self, key, data):
        validate_key(key); validate_data(data)
        name = key.split('/')[-1]
        with self._parent(key, create=True) as parent:
            temp = '.' + uuid.uuid4().hex + '.tmp'
            fd = self._open(parent, temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(data); stream.flush(); os.fsync(stream.fileno())
                if re.fullmatch(r'registry/heartbeats/[0-9a-f]{32}\.json',key):
                    # Only sealed heartbeat slots are mutable; all log objects stay immutable.
                    if isinstance(parent,int):
                        try: info=os.stat(name,dir_fd=parent,follow_symlinks=False)
                        except FileNotFoundError: pass
                        else:
                            if not stat.S_ISREG(info.st_mode): raise StorageError('regular object file required')
                        os.replace(temp,name,src_dir_fd=parent,dst_dir_fd=parent)
                    else:
                        _check(parent/name); os.replace(parent/temp,parent/name)
                    if isinstance(parent,int): os.fsync(parent)
                    return
                try:
                    if isinstance(parent, int): os.link(temp, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                    else:
                        _check(parent / name)
                        os.link(parent / temp, parent / name)
                except FileExistsError:
                    if self._read(parent, name) != data: raise ObjectExists('object already exists with different bytes')
            finally:
                try:
                    if isinstance(parent, int): os.unlink(temp, dir_fd=parent)
                    else: (parent / temp).unlink()
                except FileNotFoundError: pass
            if isinstance(parent, int): os.fsync(parent)

    def list(self, prefix, *, start_after=None):
        validate_key(prefix, prefix=True)
        if start_after is not None: validate_key(start_after)
        _check(self.root)
        keys = []
        def onerror(_): raise StorageError('filesystem listing failed')
        try:
            start = self.root
            # Only slash-terminated components name complete directories.
            for part in prefix.split('/')[:-1]:
                start = start / part
                _check(start)
                try: info = start.lstat()
                except FileNotFoundError: return iter(())
                if stat.S_ISREG(info.st_mode): return iter(())
                if not stat.S_ISDIR(info.st_mode): raise StorageError('directory required for prefix')
            for directory, dirs, files in os.walk(start, followlinks=False, onerror=onerror):
                _check(Path(directory))
                dirs[:] = [name for name in dirs if not name.startswith('.')]
                for name in dirs: _check(Path(directory) / name)
                for name in files:
                    if name.startswith('.'): continue
                    path = Path(directory) / name
                    _check(path)
                    if not path.is_file(): raise StorageError('regular object file required')
                    key = path.relative_to(self.root).as_posix()
                    try: validate_key(key)
                    except InvalidKey: continue
                    if key.startswith(prefix) and (start_after is None or key > start_after): keys.append(key)
        except OSError: raise StorageError('filesystem listing failed') from None
        return iter(sorted(keys))
