"""Profile-owned SQLite authority and a trusted, in-process Memory API.

The principal is bound by the application constructor, never a method payload.
This is API authorization, not a sandbox against arbitrary Python/filesystem use.
"""
from contextlib import contextmanager, closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid


class KnowledgeError(ValueError):
    pass


class AccessDenied(KnowledgeError):
    pass


class Conflict(KnowledgeError):
    pass


class Unavailable(KnowledgeError):
    pass


PROFILE = re.compile(r'[a-z][a-z0-9_-]{0,63}\Z')
KINDS = {'fact', 'requirement', 'decision', 'preference', 'observation', 'derived'}
SOURCE_KINDS = {'source', 'user_authored', 'tool_observation', 'model_extraction'}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return 'sha256:' + hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def profile_id(value):
    if not isinstance(value, str) or not PROFILE.fullmatch(value):
        raise KnowledgeError('invalid profile ID')
    return value


def bounded_text(value, name, maximum=8192):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or '\x00' in value:
        raise KnowledgeError('invalid ' + name)
    return value


def checked_path(path):
    path = Path(path).expanduser().absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise KnowledgeError('symlink store path refused')
    return path


def policy_for(owner, policy=None):
    policy = deepcopy(policy) if policy is not None else {'read': [owner]}
    if not isinstance(policy, dict) or set(policy) - {'read', 'evidence', 'retain', 'export', 'embed'}:
        raise KnowledgeError('invalid policy')
    result = {}
    for action in ('read', 'evidence', 'retain', 'export'):
        values = policy.get(action, policy.get('read', [owner]) if action == 'evidence' else [])
        if not isinstance(values, list) or len(values) > 64:
            raise KnowledgeError('invalid policy principals')
        result[action] = sorted({profile_id(p) for p in values} | ({owner} if action == 'read' else set()))
    if any(set(result[action]) - set(result['read']) for action in ('evidence', 'retain', 'export')):
        raise KnowledgeError('policy requires read permission')
    embed = policy.get('embed', [])
    if not isinstance(embed, list) or any(e != 'local' for e in embed):
        raise KnowledgeError('only explicit local embedding egress is supported')
    result['embed'] = sorted(set(embed))
    return result


class ProfileStore:
    def __init__(self, root, identity):
        self.profile_id = profile_id(identity)
        self.root = checked_path(root)
        marker = self.root / 'state' / 'profile.json'
        if self.root.exists() and not marker.exists() and any(self.root.iterdir()):
            raise KnowledgeError('refuse initializing a non-empty unrelated directory')
        for name in ('sources', 'knowledge', 'indexes', 'state', 'exports'):
            checked_path(self.root / name).mkdir(parents=True, exist_ok=True)
        if marker.exists():
            checked_path(marker)
            if json.loads(marker.read_text(encoding='utf-8')) != {'format': 'agentmesh-profile-v1', 'profile': identity}:
                raise KnowledgeError('profile identity mismatch')
        else:
            with marker.open('x', encoding='utf-8') as out:
                out.write(canonical({'format': 'agentmesh-profile-v1', 'profile': identity}))
        self.database = checked_path(self.root / 'knowledge' / 'knowledge.sqlite3')
        with self.connection() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS revisions (
                    id TEXT NOT NULL, revision TEXT NOT NULL, owner TEXT NOT NULL,
                    data TEXT NOT NULL, PRIMARY KEY(id, revision));
                CREATE TABLE IF NOT EXISTS heads (
                    id TEXT NOT NULL, revision TEXT NOT NULL,
                    PRIMARY KEY(id, revision),
                    FOREIGN KEY(id, revision) REFERENCES revisions(id, revision));
                CREATE TABLE IF NOT EXISTS audit (
                    sequence INTEGER PRIMARY KEY, action TEXT NOT NULL,
                    principal TEXT NOT NULL, id TEXT, revision TEXT);
            ''')

    @contextmanager
    def connection(self):
        checked_path(self.database)
        with closing(sqlite3.connect(self.database, timeout=15)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA foreign_keys=ON')
            with conn:
                yield conn

    def raw(self, ident, revision=None, conn=None):
        if conn is None:
            with self.connection() as local:
                return self.raw(ident, revision, local)
        if revision is None:
            heads = conn.execute('SELECT revision FROM heads WHERE id=? ORDER BY revision', (ident,)).fetchall()
            if len(heads) > 1:
                raise Conflict('divergent revisions require explicit resolution')
            if not heads:
                return None
            revision = heads[0]['revision']
        row = conn.execute('SELECT * FROM revisions WHERE id=? AND revision=?', (ident, revision)).fetchone()
        return dict(row) if row else None

    def put(self, record, principal, conn=None):
        if conn is None:
            with self.connection() as local:
                return self.put(record, principal, local)
        data = deepcopy(record)
        revision = data.pop('revision')
        if digest(data) != revision:
            raise KnowledgeError('revision digest mismatch')
        conn.execute('INSERT INTO revisions VALUES(?,?,?,?)',
                     (record['id'], revision, record['owner'], canonical(record)))
        for parent in record['parents']:
            conn.execute('DELETE FROM heads WHERE id=? AND revision=?', (record['id'], parent))
        conn.execute('INSERT INTO heads VALUES(?,?)', (record['id'], revision))
        conn.execute('INSERT INTO audit(action,principal,id,revision) VALUES(?,?,?,?)',
                     ('record', principal, record['id'], revision))

    def records(self):
        with self.connection() as conn:
            return [json.loads(row['data']) for row in conn.execute(
                'SELECT r.data FROM heads h JOIN revisions r USING(id,revision) ORDER BY r.id,r.revision')]


class MemoryAPI:
    def __init__(self, stores, *, principal):
        self.principal = profile_id(principal)
        self.stores = dict(stores)
        if any(key != store.profile_id for key, store in self.stores.items()):
            raise KnowledgeError('store identity mismatch')

    def _store(self, owner):
        profile_id(owner)
        if owner not in self.stores:
            raise Unavailable('profile is not available')
        return self.stores[owner]

    def _record(self, owner, ident, revision=None):
        store = self._store(owner)
        row = store.raw(ident, revision)
        if not row:
            raise AccessDenied('knowledge not accessible')
        record = json.loads(row['data'])
        current = store.raw(ident)
        # A historical permissive policy cannot bypass a current revocation.
        current_record = json.loads(current['data']) if current else record
        if self.principal != owner and (current_record['status'] != 'active' or
                self.principal not in current_record['policy']['read']):
            raise AccessDenied('knowledge not accessible')
        if self.principal not in record['policy']['read']:
            raise AccessDenied('knowledge not accessible')
        return record

    def _view(self, record):
        result = deepcopy(record)
        current = self._record(record['owner'], record['id'])
        if (self.principal not in record['policy']['evidence'] or
                self.principal not in current['policy']['evidence']):
            result['evidence'] = [{'availability': 'redacted'} for _ in record['evidence']]
        result['review_required'] = False
        for reference in record['derived_from']:
            try:
                source = self._record(reference['owner'], reference['id'])
                if source['revision'] != reference['revision'] or source['status'] != 'active':
                    result['review_required'] = True
            except KnowledgeError:
                result['review_required'] = True
        return result

    def get(self, owner, ident, revision=None):
        return self._view(self._record(owner, ident, revision))

    def evidence(self, owner, ident, revision=None):
        return self.get(owner, ident, revision)['evidence']

    def revise(self, ident, expected_revision, *, content):
        bounded_text(content, 'content')
        return self._change(ident, expected_revision, content=content)

    def set_policy(self, ident, expected_revision, policy):
        return self._change(ident, expected_revision, policy=policy_for(self.principal, policy))

    def revoke(self, ident, expected_revision):
        return self._change(ident, expected_revision, status='revoked')

    def _change(self, ident, expected_revision, **changes):
        store = self._store(self.principal)
        with store.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = store.raw(ident, conn=conn)
            if not row or row['owner'] != self.principal:
                raise AccessDenied('only the owning profile may revise')
            old = json.loads(row['data'])
            if old['revision'] != expected_revision:
                raise Conflict('stale revision')
            record = deepcopy(old)
            record.pop('revision')
            record.update(changes)
            record['parents'] = [expected_revision]
            record['revision'] = digest(record)
            store.put(record, self.principal, conn)
        return self.get(self.principal, ident)

    def retain(self, owner, ident, revision, *, content, project='default'):
        bounded_text(content, 'content')
        bounded_text(project, 'project', 128)
        source = self._record(owner, ident, revision)
        current = self._record(owner, ident)
        if (source['status'] != 'active' or self.principal not in source['policy']['retain'] or
                self.principal not in current['policy']['retain']):
            raise AccessDenied('retain permission required')
        store = self._store(self.principal)
        record = {'id': 'urn:uuid:' + str(uuid.uuid4()), 'owner': self.principal,
                  'content': content, 'kind': 'derived', 'project': project, 'status': 'active',
                  'parents': [], 'verification': 'unverified', 'evidence': [],
                  'derived_from': [{'owner': owner, 'id': ident, 'revision': revision}],
                  'policy': policy_for(self.principal)}
        record['revision'] = digest(record)
        store.put(record, self.principal)
        return self.get(self.principal, record['id'])

    def remember(self, content, *, kind='fact', project='default', source=None, quote=None, policy=None):
        owner = self.principal
        store = self._store(owner)
        bounded_text(content, 'content')
        bounded_text(project, 'project', 128)
        if kind not in KINDS:
            raise KnowledgeError('invalid knowledge kind')
        evidence = []
        if source is not None:
            if not isinstance(source, dict) or set(source) != {'text', 'kind', 'locator'}:
                raise KnowledgeError('invalid source')
            text = bounded_text(source['text'], 'source text', 32768)
            locator = bounded_text(source['locator'], 'source locator', 512)
            if source['kind'] not in SOURCE_KINDS:
                raise KnowledgeError('invalid source kind')
            quote = bounded_text(quote, 'evidence quote', 4096)
            if quote not in text:
                raise KnowledgeError('quote is not present in source')
            source_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()
            path = checked_path(store.root / 'sources' / (source_hash + '.txt'))
            if not path.exists():
                with path.open('x', encoding='utf-8') as out:
                    out.write(text)
            evidence = [{'source_id': 'sha256:' + source_hash, 'kind': source['kind'],
                         'locator': locator, 'quote': quote, 'availability': 'inline'}]
        elif quote is not None:
            raise KnowledgeError('quote requires a source')
        record = {'id': 'urn:uuid:' + str(uuid.uuid4()), 'owner': owner, 'content': content,
                  'kind': kind, 'project': project, 'status': 'active', 'parents': [],
                  'verification': 'unverified', 'evidence': evidence, 'derived_from': [],
                  'policy': policy_for(owner, policy)}
        record['revision'] = digest(record)
        store.put(record, owner)
        return self.get(owner, record['id'])
