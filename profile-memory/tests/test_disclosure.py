"""Real SQLite disclosure fences; fixture vectors are mechanics only."""
from contextlib import contextmanager, closing
import sqlite3

import pytest

from agentmesh_memory.core import MemoryAPI, ProfileStore, Unavailable


@pytest.mark.parametrize('count', [1, 17])
def test_rebuild_serializes_disclosure_and_aborts_revoked_later_batch(tmp_path, count):
    store = ProfileStore(tmp_path / 'alpha', 'alpha')
    api = MemoryAPI({'alpha': store}, principal='alpha')
    records = [api.remember('Fixture secret ' + str(i), policy={'embed': ['local']}) for i in range(count)]
    target = max(records, key=lambda r: r['id'])
    writer = MemoryAPI({'alpha': ProfileStore(store.root, 'alpha')}, principal='alpha')
    original_connection = store.connection
    changed = []
    calls = []
    locked = []

    @contextmanager
    def after_disclosure():
        with original_connection() as conn:
            yield conn
            held_transaction = conn.in_transaction
        if held_transaction and not changed:
            changed.append(True)
            writer.set_policy(target['id'], target['revision'], {'embed': []})

    store.connection = after_disclosure

    class Provider:
        space = {'provider': 'fixture', 'model': 'test', 'revision': 'v1', 'dimension': 2, 'metric': 'cosine'}

        def embed(self, texts):
            calls.append(list(texts))
            with closing(sqlite3.connect(store.database, timeout=0)) as competing:
                try:
                    competing.execute('BEGIN IMMEDIATE')
                except sqlite3.OperationalError as exc:
                    assert 'locked' in str(exc)
                    locked.append(True)
                else:
                    competing.rollback()
                    locked.append(False)
            return [[1.0, 0.0] for _ in texts]

    with pytest.raises(Unavailable, match='changed'):
        api.rebuild_index(Provider())
    assert locked == [True]
    assert changed == [True]
    assert len(calls) == 1
    if count == 17:
        assert target['content'] not in calls[0]
    else:
        # Already authorized in-flight disclosure cannot be undone retroactively.
        assert target['content'] in calls[0]
    assert not (store.root / 'indexes' / 'vectors.sqlite3').exists()
