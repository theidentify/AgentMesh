"""Standalone trusted-local CLI. This is not a network authentication service."""
import argparse
import json
from pathlib import Path
import os
import secrets
import sqlite3
import sys

from .core import (MemoryAPI, ProfileStore, KnowledgeError, Unavailable, Conflict,
                   canonical, checked_path, profile_id)


def read_json(path):
    path = checked_path(path)
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise KnowledgeError('duplicate JSON key')
            result[key] = value
        return result
    with path.open('rb') as source:
        raw = source.read(1048577)
    if len(raw) > 1048576:
        raise KnowledgeError('JSON input exceeds 1 MiB')
    value = json.loads(raw, object_pairs_hook=unique_pairs)
    if not isinstance(value, dict):
        raise KnowledgeError('JSON object required')
    return value


def registry(root, principal):
    root = checked_path(root)
    profile_id(principal)
    primary = root / 'profiles' / principal
    if not (primary / 'state' / 'profile.json').is_file():
        raise Unavailable('initialize the selected prototype profile first')
    stores = {}
    for directory in sorted((root / 'profiles').iterdir()):
        if not (directory / 'state' / 'profile.json').is_file():
            continue
        identity = profile_id(directory.name)
        stores[identity] = ProfileStore(directory, identity)
    mirrors = checked_path(root / 'mirrors' / principal)
    if mirrors.exists():
        for directory in sorted(mirrors.iterdir()):
            if not (directory / 'state' / 'profile.json').is_file():
                continue
            identity = profile_id(directory.name)
            if identity in stores:
                raise Conflict('ambiguous local authority and mirror identity')
            stores[identity] = ProfileStore(directory, identity)
    return MemoryAPI(stores, principal=principal)


def read_key(path):
    with checked_path(path).open('rb') as source:
        value = source.read(4097)
    if not 32 <= len(value) <= 4096:
        raise KnowledgeError('channel key file must contain 32..4096 raw bytes')
    return value


def write_json(path, value):
    path = checked_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as target:
        target.write(canonical(value) + '\n')
    return str(path)


def parser():
    result = argparse.ArgumentParser(description='[o-A-o] AgentMesh | isolated profile memory prototype')
    result.add_argument('--root', type=Path, required=True, help='Dedicated prototype workspace outside production')
    result.add_argument('--profile', default='alpha', help='Trusted local application principal, not a network credential')
    commands = result.add_subparsers(dest='command', required=True)
    demo = commands.add_parser('demo', help='Exercise a new fixture-only two-profile exchange workspace')
    demo.add_argument('--with-embeddings', action='store_true')
    demo.add_argument('--model', default='bge-m3:latest')
    demo.add_argument('--endpoint', default='http://127.0.0.1:11434')
    commands.add_parser('init', help='Initialize one standalone prototype profile')
    remember = commands.add_parser('remember', help='Record validated, unverified knowledge')
    remember.add_argument('--input', type=Path, required=True)
    for name in ('get', 'evidence', 'related'):
        get = commands.add_parser(name, help='Read authorized knowledge or provenance')
        get.add_argument('--owner', required=True)
        get.add_argument('--id', required=True)
        get.add_argument('--revision')
    retain = commands.add_parser('retain', help='Persist selected derived memory with retain permission')
    retain.add_argument('--owner', required=True)
    retain.add_argument('--id', required=True)
    retain.add_argument('--revision', required=True)
    retain.add_argument('--input', type=Path, required=True)
    revise = commands.add_parser('revise', help='Create a correction with an explicit expected revision')
    revise.add_argument('--id', required=True)
    revise.add_argument('--expected', required=True)
    revise.add_argument('--input', type=Path, required=True)
    revoke = commands.add_parser('revoke', help='Revoke an object without erasing revision history')
    revoke.add_argument('--id', required=True)
    revoke.add_argument('--expected', required=True)
    search = commands.add_parser('search', help='Search authorized current knowledge')
    search.add_argument('query')
    search.add_argument('--mode', choices=('keyword', 'semantic', 'hybrid'))
    search.add_argument('--engine', choices=('exact', 'hnsw'))
    search.add_argument('--project')
    search.add_argument('--owner', action='append')
    search.add_argument('--limit', type=int, default=12)
    search.add_argument('--context-chars', type=int, default=8192)
    search.add_argument('--expand-relations', action='store_true')
    search.add_argument('--model', default='bge-m3:latest')
    search.add_argument('--endpoint', default='http://127.0.0.1:11434')
    configure = commands.add_parser('configure', help='Set profile retrieval defaults without implicit fallback')
    configure.add_argument('--mode', choices=('keyword', 'semantic', 'hybrid'), required=True)
    configure.add_argument('--engine', choices=('exact', 'hnsw'), default='exact')
    index = commands.add_parser('index', help='Explicitly rebuild an owner-local approved embedding projection')
    index.add_argument('--issuer', help='Rebuild an authorized receiver-partitioned mirror projection')
    index.add_argument('--model', default='bge-m3:latest')
    index.add_argument('--endpoint', default='http://127.0.0.1:11434')
    keygen = commands.add_parser('keygen', help='Create a private random HMAC channel key; never overwrite')
    keygen.add_argument('--output', type=Path, required=True)
    export = commands.add_parser('export', help='Write an authorized immutable semantic bundle')
    export.add_argument('--recipient', required=True)
    export.add_argument('--id', action='append', required=True)
    export.add_argument('--key-file', type=Path, required=True)
    export.add_argument('--output', type=Path, required=True)
    export.add_argument('--no-history', action='store_true')
    apply = commands.add_parser('apply', help='Apply a pinned owner channel to a separate read-only mirror')
    apply.add_argument('--issuer', required=True)
    apply.add_argument('--key-file', type=Path, required=True)
    apply.add_argument('--input', type=Path, required=True)
    retry = commands.add_parser('retry', help='Retry authenticated persisted pending packets to fixed point')
    retry.add_argument('--issuer', required=True)
    retry.add_argument('--key-file', type=Path, required=True)
    status = commands.add_parser('status', help='Show content-free receipt/pending metadata')
    status.add_argument('--issuer')
    return result


def dispatch(args):
    root = checked_path(args.root)
    identity = profile_id(args.profile)
    if args.command == 'demo':
        from .demo import run
        provider = None
        if args.with_embeddings:
            from .embeddings import OllamaEmbedding
            provider = OllamaEmbedding(model=args.model, base_url=args.endpoint)
        return run(root, provider)
    if args.command == 'init':
        store = ProfileStore(root / 'profiles' / identity, identity)
        return {'profile': identity, 'root': str(store.root), 'format': 'agentmesh-profile-v1'}
    if args.command == 'keygen':
        path = checked_path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as target:
            target.write(secrets.token_bytes(32))
        return {'key_file': str(path), 'bytes': 32, 'authentication': 'pre-shared HMAC; not asymmetric'}
    api = registry(root, identity)
    if args.command == 'export':
        from .exchange import export_bundle
        bundle = export_bundle(api, args.recipient, args.id, key=read_key(args.key_file), include_history=not args.no_history)
        output = write_json(args.output, bundle)
        return {'bundle_id': bundle['bundle_id'], 'output': output, 'records': len(bundle['records']),
                'issuer': bundle['issuer'], 'recipient': bundle['recipient']}
    if args.command == 'apply':
        from .exchange import apply_bundle
        issuer = profile_id(args.issuer)
        if issuer == identity:
            raise KnowledgeError('self-channel imports are outside this cross-profile prototype')
        mirror_path = checked_path(root / 'mirrors' / identity / issuer)
        if issuer in api.stores and api.stores[issuer].root != mirror_path:
            raise Conflict('refuse shadowing a local authoritative profile with a mirror')
        bundle, key = read_json(args.input), read_key(args.key_file)
        mirror = ProfileStore(mirror_path, issuer)
        return apply_bundle(mirror, bundle, recipient=identity, trusted_issuer=issuer, key=key)
    if args.command == 'retry':
        from .exchange import retry_pending
        issuer = profile_id(args.issuer)
        store = api._store(issuer)
        return retry_pending(store, recipient=identity, trusted_issuer=issuer, key=read_key(args.key_file))
    if args.command == 'status':
        from .exchange import pending
        from .retrieval import candidates
        owner = args.issuer or identity
        store = api._store(owner)
        receipts = {}
        with store.connection() as conn:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='exchange_receipts'").fetchone()
            if exists:
                receipts = {row['status']: row['n'] for row in conn.execute('SELECT status,count(*) n FROM exchange_receipts GROUP BY status')}
        readable, _ = candidates(api, owners=[owner])
        return {'profile': identity, 'owner': owner, 'readable_current': len(readable),
                'pending': len(pending(store)), 'receipts': receipts}
    if args.command == 'configure':
        return api.configure_retrieval(mode=args.mode, vector_engine=args.engine)
    if args.command == 'index':
        from .embeddings import OllamaEmbedding
        return api.rebuild_index(OllamaEmbedding(model=args.model, base_url=args.endpoint), owner=args.issuer)
    if args.command == 'remember':
        return api.remember(**read_json(args.input))
    if args.command in ('get', 'evidence', 'related'):
        return getattr(api, args.command)(args.owner, args.id, args.revision)
    if args.command == 'retain':
        return api.retain(args.owner, args.id, args.revision, **read_json(args.input))
    if args.command == 'revise':
        return api.revise(args.id, args.expected, **read_json(args.input))
    if args.command == 'revoke':
        return api.revoke(args.id, args.expected)
    if args.command == 'search':
        from .retrieval import defaults
        mode = args.mode or defaults(api)['mode']
        provider = None
        if mode != 'keyword':
            from .embeddings import OllamaEmbedding
            provider = OllamaEmbedding(model=args.model, base_url=args.endpoint)
        return api.search(args.query, mode=args.mode, vector_engine=args.engine, project=args.project,
                          owners=args.owner, limit=args.limit, context_chars=args.context_chars,
                          expand_relations=args.expand_relations, provider=provider)
    raise KnowledgeError('unknown command')


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = dispatch(args)
        print(canonical(result))
        return 0
    except (KnowledgeError, ValueError, TypeError, KeyError, RuntimeError, OSError, sqlite3.Error) as exc:
        print(canonical({'error': type(exc).__name__, 'message': str(exc)}), file=sys.stderr)
        return 1
