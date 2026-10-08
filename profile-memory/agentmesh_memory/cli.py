"""Standalone trusted-local CLI. This is not a network authentication service."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

from .core import MemoryAPI, ProfileStore, KnowledgeError, Unavailable, canonical, checked_path, profile_id


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
    return MemoryAPI(stores, principal=principal)


def parser():
    result = argparse.ArgumentParser(description='[o-A-o] AgentMesh | isolated profile memory prototype')
    result.add_argument('--root', type=Path, required=True, help='Dedicated prototype workspace outside production')
    result.add_argument('--profile', default='alpha', help='Trusted local application principal, not a network credential')
    commands = result.add_subparsers(dest='command', required=True)
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
    index.add_argument('--model', default='bge-m3:latest')
    index.add_argument('--endpoint', default='http://127.0.0.1:11434')
    return result


def dispatch(args):
    root = checked_path(args.root)
    identity = profile_id(args.profile)
    if args.command == 'init':
        store = ProfileStore(root / 'profiles' / identity, identity)
        return {'profile': identity, 'root': str(store.root), 'format': 'agentmesh-profile-v1'}
    api = registry(root, identity)
    if args.command == 'configure':
        return api.configure_retrieval(mode=args.mode, vector_engine=args.engine)
    if args.command == 'index':
        from .embeddings import OllamaEmbedding
        return api.rebuild_index(OllamaEmbedding(model=args.model, base_url=args.endpoint))
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
