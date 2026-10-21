"""Owner-consented, in-memory tar backups encrypted to device and recovery keys.

Restore uses only the local device identity. Recovery-identity decryption requires
the recovery kit and is outside this module's scope. No plaintext backup archive
is written to disk; only an explicitly consented restore writes plaintext files.
"""

import argparse
from contextlib import contextmanager
import hashlib
import io
import json
import ntpath
import os
from pathlib import Path
import shutil
import stat
import sys
import tarfile

import pyrage

from . import oskeystore
from .agekeys import DeviceAgeIdentity, validate_recipient
from .broker.keystore import BrokerHome


class BackupError(ValueError):
    """Messages are fixed codes, never underlying exception text."""


class Declined(Exception):
    pass


def _path(value):
    return Path(os.path.abspath(value))


@contextmanager
def _directory(path):
    """Walk with directory descriptors so no path component follows a symlink."""
    path = _path(path)
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _state(path):
    # BrokerHome creates absent directories. Preflight every directory it would
    # create, so opening an incomplete/nonexistent home fails without writing it.
    for directory in (path, *(path / name for name in ('agents', 'replica', 'index'))):
        with _directory(directory):
            pass
    return oskeystore.select_store(BrokerHome(path)).load()


def _recipients(state):
    registry = state.registry()
    # Registry loading deliberately ignores invalid records. Do not silently use
    # an older recovery key if a newer cached record failed validation.
    records = state.control.get('registry_records', {})
    cached = {key for key in records if key.startswith('registry/recovery/')}
    verified = {'registry/recovery/' + key + '.json' for key in registry.recoveries}
    if not cached or cached != verified:
        raise BackupError('invalid_recipients')
    device = registry.device_records[state.device.device_id]['age_recipient']
    recovery = registry.current_recovery()['recovery_age_recipient']
    identity = DeviceAgeIdentity.parse(state.control['age_identity'])
    if identity.recipient != device:
        raise BackupError('invalid_recipients')
    return [validate_recipient(device), validate_recipient(recovery)]


def _answer():
    # Never accept piped input as owner consent. Lack of a controlling terminal
    # is a refusal; noninteractive explicit owner use must supply --yes.
    try:
        with open('/dev/tty', 'r', encoding='utf-8') as terminal:
            return terminal.readline()
    except (OSError, EOFError):
        return ''


def _consent(yes, operation, files, size, destination):
    if yes:
        return
    # JSON quoting keeps even a destination containing newlines on one line.
    print(json.dumps(dict(files=files, bytes=size, destination=str(destination),
                          recipients=['device', 'recovery'])), file=sys.stderr)
    print(f'{operation} encrypted backup? [y/N]', end=' ', file=sys.stderr, flush=True)
    answer = _answer().strip().lower()
    print(file=sys.stderr)
    if answer not in ('y', 'yes'):
        raise Declined()


def _safe_name(name, names):
    parts = name.split('/')
    if (not name or name.startswith('/') or '\\' in name or '\x00' in name
            or ntpath.splitdrive(name)[0] or any(p in ('', '.', '..') for p in parts)):
        raise BackupError('unsafe_archive')
    if name in names or any('/'.join(parts[:i]) in names for i in range(1, len(parts))):
        raise BackupError('conflicting_paths')
    if any(previous.startswith(name + '/') for previous in names):
        raise BackupError('conflicting_paths')
    names.add(name)


def _pack(includes):
    buffer = io.BytesIO()
    names = set()
    total = 0
    with tarfile.open(fileobj=buffer, mode='w') as archive:
        def visit(parent, leaf, name):
            nonlocal total
            info = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                fd = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    for child in sorted(os.listdir(fd)):
                        visit(fd, child, name + '/' + child)
                finally:
                    os.close(fd)
                return
            if not stat.S_ISREG(info.st_mode):
                raise BackupError('unsafe_source')
            fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise BackupError('unsafe_source')
                data = stream.read()
            _safe_name(name, names)
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.mode = stat.S_IMODE(info.st_mode)
            member.mtime = int(info.st_mtime)
            member.uid = member.gid = 0
            member.uname = member.gname = ''
            archive.addfile(member, io.BytesIO(data))
            total += len(data)

        for source in includes:
            with _directory(source.parent) as parent:
                visit(parent, source.name, source.name)
    return buffer.getvalue(), len(names), total


def _absent(parent, name):
    try:
        os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise BackupError('destination_exists')


def _create(args, state, home):
    output = _path(args.out)
    includes = [_path(value) for value in args.include]
    if output.is_relative_to(home) or any(output.is_relative_to(source) for source in includes):
        raise BackupError('unsafe_destination')
    recipients = _recipients(state)
    with _directory(output.parent) as parent:
        info = os.fstat(parent)
        if stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.getuid():
            raise BackupError('unsafe_destination')
        _absent(parent, output.name)
        plaintext, count, size = _pack(includes)
        _consent(args.yes, 'Create', count, size, output)
        ciphertext = pyrage.encrypt(plaintext, recipients)
        del plaintext
        fd = os.open(output.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(ciphertext)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            os.unlink(output.name, dir_fd=parent)
            raise
    return dict(ok=True, files=count, bytes=size, recipients=['device', 'recovery'],
                sha256=hashlib.sha256(ciphertext).hexdigest())


def _unpack(plaintext):
    files = []
    names = set()
    with tarfile.open(fileobj=io.BytesIO(plaintext), mode='r:') as archive:
        for member in archive:
            if not member.isfile() or member.issparse():
                raise BackupError('unsafe_archive')
            _safe_name(member.name, names)
            with archive.extractfile(member) as stream:
                data = stream.read()
            if len(data) != member.size:
                raise BackupError('invalid_archive')
            files.append((member.name, member.mode & 0o7777, data))
    return files


def _extract(root, files):
    for name, mode, data in files:
        parts = name.split('/')
        parent = os.dup(root)
        try:
            for part in parts[:-1]:
                try:
                    os.mkdir(part, 0o700, dir_fd=parent)
                except FileExistsError:
                    pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                os.close(parent)
                parent = child
            fd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=parent)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fchmod(stream.fileno(), mode)
        finally:
            os.close(parent)


def _restore(args, state, home):
    target, source = _path(args.to), _path(args.input)
    if target.is_relative_to(home):
        raise BackupError('unsafe_destination')
    with _directory(target.parent) as parent:
        _absent(parent, target.name)
        with _directory(source.parent) as source_parent:
            fd = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=source_parent)
            with os.fdopen(fd, 'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise BackupError('unsafe_source')
                ciphertext = stream.read()
        identity = DeviceAgeIdentity.parse(state.control['age_identity'])
        files = _unpack(identity.decrypt(ciphertext))
        size = sum(len(data) for _, _, data in files)
        _consent(args.yes, 'Restore', len(files), size, target)
        os.mkdir(target.name, 0o700, dir_fd=parent)
        try:
            root = os.open(target.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                os.fchmod(root, 0o700)
                _extract(root, files)
            finally:
                os.close(root)
        except BaseException:
            shutil.rmtree(target.name, dir_fd=parent)
            raise
    return dict(ok=True, files=len(files), bytes=size)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise BackupError('invalid_arguments')


def main(argv=None):
    try:
        parser = _Parser(description=__doc__)
        commands = parser.add_subparsers(dest='command', required=True)
        for command in ('create', 'restore'):
            sub = commands.add_parser(command)
            sub.add_argument('--home', required=True)
            sub.add_argument('--yes', action='store_true')
            if command == 'create':
                sub.add_argument('--out', required=True)
                sub.add_argument('--include', action='append', required=True)
            else:
                sub.add_argument('--in', dest='input', required=True)
                sub.add_argument('--to', required=True)
        args = parser.parse_args(argv)
        # Descriptor-relative walking (O_DIRECTORY, dir_fd, /dev/tty) is POSIX-only.
        if os.name != 'posix': raise BackupError('unsupported_platform')
        home = _path(args.home)
        state = _state(home)
        result = (_create if args.command == 'create' else _restore)(args, state, home)
    except (Declined, KeyboardInterrupt):
        return 3
    except Exception as error:
        result = dict(ok=False, error=str(error) if isinstance(error, BackupError) else 'backup_failed')
        print(json.dumps(result, separators=(',', ':')))
        return 2
    print(json.dumps(result, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
