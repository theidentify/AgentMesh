"""Repeatable fixture-only two-profile demo. No production paths or services."""
import json
import secrets

from .core import AccessDenied, Conflict, KnowledgeError, MemoryAPI, ProfileStore, canonical, checked_path
from .exchange import apply_bundle, export_bundle, pending, retry_pending


def run(root, provider=None):
    root = checked_path(root)
    if root.exists():
        raise KnowledgeError('demo requires a new dedicated workspace')
    source = ProfileStore(root / 'source-node' / 'profiles' / 'alpha', 'alpha')
    personal = ProfileStore(root / 'receiver-node' / 'profiles' / 'beta', 'beta')
    mirror = ProfileStore(root / 'receiver-node' / 'mirrors' / 'beta' / 'alpha', 'alpha')
    author = MemoryAPI({'alpha': source}, principal='alpha')
    reader = MemoryAPI({'alpha': mirror, 'beta': personal}, principal='beta')
    key = secrets.token_bytes(32)  # Ephemeral demo channel, never printed or exported.
    policy = {'read': ['alpha', 'beta'], 'evidence': ['alpha', 'beta'],
              'retain': ['beta'], 'export': ['beta'], 'embed': ['local']}
    checks = {'isolated_authorities': source.database != personal.database != mirror.database}

    def bundle(api, ident, history=True):
        value = export_bundle(api, 'beta', [ident], key=key, include_history=history)
        path = api._store('alpha').root / 'exports' / (value['bundle_id'] + '.json')
        if not path.exists():
            with path.open('x', encoding='utf-8') as target:
                target.write(canonical(value) + '\n')
        return json.loads(path.read_text(encoding='utf-8'))

    def apply(value, target=mirror):
        return apply_bundle(target, value, recipient='beta', trusted_issuer='alpha', key=key)

    def denied(operation):
        try:
            operation()
        except (AccessDenied, Conflict):
            return True
        return False

    original = author.remember('Provider A is required.', kind='requirement', project='demo',
                               source={'text': 'Provider A is required.', 'kind': 'user_authored',
                                       'locator': 'fixture://requirements/v1'},
                               quote='Provider A is required.', policy=policy)
    first = bundle(author, original['id'])
    checks['initial_applied'] = apply(first)['status'] == 'applied'
    checks['replay_duplicate'] = apply(first)['status'] == 'duplicate'
    checks['inline_evidence_readable'] = bool(reader.evidence('alpha', original['id']))
    retained = reader.retain('alpha', original['id'], original['revision'],
                             content='My task interpretation uses provider A.', project='demo')
    checks['retained_revision_pinned'] = retained['derived_from'][0]['revision'] == original['revision']
    correction = author.revise(original['id'], original['revision'], content='Any configured provider is allowed.')
    missing_parent = bundle(author, original['id'], history=False)
    current = author.revise(original['id'], correction['revision'], content='The provider must be configurable and switchable.')
    delta = bundle(author, original['id'], history=False)
    checks['out_of_order_pending'] = apply(delta)['status'] == 'pending'
    checks['pending_blocks_old_reads'] = denied(lambda: reader.get('alpha', original['id']))
    checks['pending_marks_derived_review'] = reader.get('beta', retained['id'])['review_required']
    checks['dependency_applied'] = apply(missing_parent)['status'] == 'applied'
    retries = retry_pending(mirror, recipient='beta', trusted_issuer='alpha', key=key)
    checks['pending_retried_applied'] = bool(retries) and retries[-1]['status'] == 'applied' and not pending(mirror)
    checks['exact_current_revision'] = reader.get('alpha', original['id'])['revision'] == current['revision']
    checks['derived_content_preserved'] = reader.get('beta', retained['id'])['content'] == retained['content']
    checks['derived_review_required'] = reader.get('beta', retained['id'])['review_required']
    private = author.remember('Private provider fixture.', project='demo')
    checks['private_read_denied'] = denied(lambda: reader.get('alpha', private['id']))
    checks['private_export_denied'] = denied(lambda: export_bundle(author, 'beta', [private['id']], key=key))

    configurations = [('keyword', 'exact')]
    if provider is not None:
        reader.rebuild_index(provider, owner='alpha')
        configurations += [('semantic', 'exact'), ('semantic', 'hnsw'), ('hybrid', 'exact'), ('hybrid', 'hnsw')]
    retrieval = {}
    for mode, engine in configurations:
        result = reader.search('configurable provider', mode=mode, vector_engine=engine, provider=provider,
                               owners=['alpha'], project='demo', limit=3)
        correct = bool(result['results']) and result['results'][0]['revision'] == current['revision']
        retrieval[mode + '-' + engine] = {'top1_correct': correct, 'context_chars': result['context_chars_used']}
        if not correct:
            raise RuntimeError('demo retrieval acceptance failed')

    revoked = author.revoke(original['id'], current['revision'])
    checks['revocation_applied'] = apply(bundle(author, original['id']))['status'] == 'applied'
    checks['revoked_read_denied'] = denied(lambda: reader.get('alpha', original['id']))
    checks['historical_read_denied'] = denied(lambda: reader.get('alpha', original['id'], original['revision']))
    checks['revoked_index_not_recalled'] = not reader.search('configurable provider', mode='keyword', owners=['alpha'])['results']
    if provider is not None:
        checks['revoked_semantic_not_recalled'] = not reader.search('configurable provider', mode='semantic',
                                                                    provider=provider, owners=['alpha'])['results']

    fork_store = ProfileStore(root / 'fork-node' / 'profiles' / 'alpha', 'alpha')
    fork = MemoryAPI({'alpha': fork_store}, principal='alpha')
    conflict_base = author.remember('Original decision.', project='conflict-demo', policy=policy)
    seed = bundle(author, conflict_base['id'])
    apply(seed)
    apply(seed, fork_store)
    author.revise(conflict_base['id'], conflict_base['revision'], content='Decision A.')
    apply(bundle(author, conflict_base['id']))
    fork.revise(conflict_base['id'], conflict_base['revision'], content='Decision B.')
    checks['divergent_heads_visible'] = apply(bundle(fork, conflict_base['id']))['status'] == 'conflict'
    checks['conflicting_read_denied'] = denied(lambda: reader.get('alpha', conflict_base['id']))
    for store in (source, personal, mirror, fork_store):
        with store.connection() as conn:
            if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or conn.execute('PRAGMA foreign_key_check').fetchall():
                raise RuntimeError('demo SQLite integrity failure')
    checks['sqlite_integrity'] = True
    if not all(checks.values()):
        raise RuntimeError('demo acceptance failed: ' + ', '.join(k for k, v in checks.items() if not v))
    return {'format': 'agentmesh-profile-demo-v1', 'root': str(root), 'checks': checks,
            'retrieval': retrieval, 'source_id': original['id'], 'derived_id': retained['id'],
            'revoked_revision': revoked['revision'], 'embedding_space': provider.space if provider else None,
            'limitations': ['Local fixture workspaces, not a native Windows/Linux or remote-peer test.',
                            'Trusted local API and pre-shared HMAC, not OS sandbox or network authentication.',
                            'Inline evidence is not possession of the original source file.']}
