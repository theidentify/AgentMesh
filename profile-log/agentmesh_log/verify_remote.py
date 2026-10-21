"""Read-only remote inventory, plaintext leak check, and disposable restore rehearsal.

No broker is constructed: its initialization can write indexes and replica wraps.
Listings are point-in-time observations, not an atomic snapshot of a running broker.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile

from . import cli
from .broker.grants import names
from .broker.keystore import BrokerHome
from .broker.service import PREFIXES
from .encoding import parse
from .events import event_path, open_event, verify_event, wrap_path
from .keys import unwrap_key
from .oskeystore import select_store
from .storage.filesystem import FilesystemStorage, _check


def existing_directory(root):
    root = Path(os.path.abspath(root))
    for path in reversed((root, *root.parents)):
        _check(path)
    if not root.is_dir():
        raise ValueError('existing directory required')
    return root


class ReadOnlyHome(BrokerHome):
    def __init__(self, root):
        self.root = existing_directory(root)
        self.check()


class ReadOnlyFilesystem(FilesystemStorage):
    def __init__(self, root):
        self.root = existing_directory(root)

    def put(self, key, data):
        raise ValueError('read only')


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally echoes arbitrary argv values and paths to stderr.
        raise ValueError('invalid arguments')


def arguments(argv):
    parser = Parser(description=__doc__, add_help=False)
    parser.add_argument('--home', required=True)
    remote = parser.add_mutually_exclusive_group(required=True)
    remote.add_argument('--filesystem')
    remote.add_argument('--s3-bucket')
    parser.add_argument('--s3-prefix', default='')
    parser.add_argument('--s3-endpoint')
    parser.add_argument('--s3-credentials')
    parser.add_argument('--markers', required=True)
    parser.add_argument('--sample-db')
    parser.add_argument('--sample', type=int)
    args = parser.parse_args(argv)
    if (args.sample_db is None) != (args.sample is None) or (args.sample is not None and args.sample < 0):
        raise ValueError('invalid sampling arguments')
    return args


def probes_for(args, state):
    path = Path(args.markers)
    _check(path)
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'r', encoding='utf-8') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('regular markers file required')
        probes = {line.rstrip('\r\n') for line in stream if line.strip()}
    probes.update(names(state).values())  # Includes profile roots and every full scope path.
    probes.update(state.control.get('agent_names', {}))
    if args.sample_db is not None:
        connection = sqlite3.connect(Path(args.sample_db).resolve().as_uri() + '?mode=ro', uri=True)
        try:
            connection.execute('PRAGMA query_only=ON')
            connection.execute('PRAGMA temp_store=MEMORY')
            connection.create_function('eligible', 1, lambda v: isinstance(v, str) and len(v.split()) >= 6)
            rows = connection.execute("SELECT content FROM memory_items WHERE status='active' "
                                      'AND eligible(content) ORDER BY random() LIMIT ?', (args.sample,))
            probes.update(content[:40] for (content,) in rows)
        finally:
            connection.close()
    # Case-sensitive and >= 6 chars: shorter or case-folded probes hit random base64 ciphertext by chance.
    return {probe.encode('utf-8') for probe in probes if len(probe) >= 6}


def inventory(storage):
    return {key for prefix in PREFIXES for key in storage.list(prefix)}


def verify(args):
    home = ReadOnlyHome(args.home)
    state = select_store(home).load()
    local = ReadOnlyFilesystem(home.root / 'replica')
    remote = cli.remote_storage(args, filesystem_factory=ReadOnlyFilesystem)
    probes = probes_for(args, state)
    registry = state.registry()
    local_keys, remote_keys = inventory(local), set(remote.list(''))
    replica_keys = {key for key in remote_keys if key.startswith(PREFIXES)}
    result = dict(ok=False, local_objects=len(local_keys), remote_objects=len(remote_keys),
                  missing_remote=len(local_keys - replica_keys), missing_local=len(replica_keys - local_keys),
                  unknown_remote=len(remote_keys - replica_keys),
                  hash_mismatch=0, unparsable=0, probes=len(probes), marker_hits=0,
                  restored_events=0, opened_events=0)
    read_keys = {(k.profile_id, k.scope_id, k.key_id): k
                 for tree in state.trees for epochs in tree.epochs.values() for k in epochs}
    with tempfile.TemporaryDirectory(prefix='agentmesh-verify-') as directory:
        restored = FilesystemStorage(directory)
        wraps = []
        events = []
        for key in sorted(remote_keys):
            data = remote.get(key)
            if any(probe in data for probe in probes):
                result['marker_hits'] += 1
            if key not in replica_keys:
                continue
            if key in local_keys and hashlib.sha256(data).digest() != hashlib.sha256(local.get(key)).digest():
                result['hash_mismatch'] += 1
            if key.startswith(('events/', 'keys/')):
                restored.put(key, data)
            try:
                if key.startswith('events/'):
                    event = verify_event(data, registry)
                    if event_path(event) != key:
                        raise ValueError('event path mismatch')
                    events.append(key)
                elif key.startswith('keys/'):
                    wrap = parse(data)
                    if wrap_path(wrap) != key:
                        raise ValueError('wrap path mismatch')
                    wraps.append(key)
                else:
                    parse(data)
            except Exception:
                result['unparsable'] += 1
        # Recover additional epochs through remote wraps, including multi-level chains.
        pending = wraps
        while pending:
            remaining = []
            progress = False
            for key in pending:
                wrap = parse(restored.get(key))
                parent = read_keys.get((wrap['profile'], wrap['parent_scope'], wrap['parent_key_id']))
                if parent is None:
                    remaining.append(key)
                    continue
                try:
                    child = unwrap_key(wrap, parent)
                except Exception:
                    continue
                identity = (child.profile_id, child.scope_id, child.key_id)
                if identity not in read_keys:
                    read_keys[identity] = child
                    progress = True
            if not progress:
                break
            pending = remaining
        result['restored_events'] = len(list(restored.list('events/')))
        for key in events:
            try:
                data = restored.get(key)
                event = verify_event(data, registry)
                if event_path(event) != key:
                    raise ValueError('event path mismatch')
                read_key = read_keys[(event['profile'], event['scope'], event['scope_key_id'])]
                open_event(data, registry, read_key)
            except Exception:
                continue
            result['opened_events'] += 1
    local_events = sum(key.startswith('events/') for key in local_keys)
    result['ok'] = (not any(result[k] for k in ('missing_remote', 'missing_local', 'hash_mismatch',
                                               'unparsable', 'marker_hits'))
                    and result['opened_events'] == result['restored_events'] == local_events)
    return result


def main(argv=None):
    try:
        result = verify(arguments(argv))
    except Exception:
        # Never interpolate exception messages: they may contain plaintext or credentials.
        result = {'ok': False, 'error': 'verification_error'}
        code = 2
    else:
        code = 0 if result['ok'] else 1
    print(json.dumps(result, separators=(',', ':')))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
