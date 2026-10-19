"""Opt-in real R2 check. Run with -s to see the counts-only teardown summary."""
import logging
import os
import secrets
import time
import pytest
from agentmesh_log import ulid
from agentmesh_log.errors import LogError
from agentmesh_log.events import seal_event, verify_event, open_event, event_path
from agentmesh_log.model import Label
from agentmesh_log.storage import ObjectNotFound
from agentmesh_log.storage.s3 import S3Storage
from r2_support import build_client, cleanup_run

pytestmark = pytest.mark.skipif(os.environ.get('AGENTMESH_R2_LIVE') != '1',
                               reason='live R2 requires AGENTMESH_R2_LIVE=1')


class Run:
    def __init__(self, store):
        self.store = store
        self.puts = self.gets = self.lists = self.events = 0

    def put(self, key, data):
        self.store.put(key, data)
        self.puts += 1

    def get(self, key):
        data = self.store.get(key)
        self.gets += 1
        return data

    def list(self, prefix, **kwargs):
        keys = list(self.store.list(prefix, **kwargs))
        self.lists += 1
        return keys


@pytest.fixture(scope='module')
def r2_run(request):
    __tracebackhide__ = True
    started = time.monotonic()
    # Prevent SDK debug logging of request authorization or credential details.
    old_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        client, bucket = build_client(os.environ)
        prefix = f'agentmesh-live-tests/{ulid.generate()}/'
        run = Run(S3Storage(bucket, prefix=prefix, client=client))
    except Exception:
        logging.disable(old_disable)
        raise

    def finish():
        __tracebackhide__ = True
        deleted = 0
        try:
            deleted = cleanup_run(client, bucket, prefix)
            assert list(run.store.list('')) == [], 'run prefix cleanup incomplete'
        finally:
            logging.disable(old_disable)
            print(f'puts={run.puts} gets={run.gets} lists={run.lists} events={run.events} '
                  f'deleted={deleted} elapsed={time.monotonic() - started:.2f}s')
    request.addfinalizer(finish)
    return run


def test_contract(r2_run):
    run = r2_run
    payload = secrets.token_bytes(128)
    run.put('contract/roundtrip', payload)
    assert run.get('contract/roundtrip') == payload
    with pytest.raises(ObjectNotFound): run.get('contract/missing')
    keys = ['nested/z', 'nested/a/b', 'nested/a/a', 'nested/c', 'nested/b']
    for key in keys: run.put(key, secrets.token_bytes(32))
    assert run.list('nested/') == sorted(keys)
    assert run.list('nested/a/') == ['nested/a/a', 'nested/a/b']
    assert run.list('no-match/') == []
    assert run.list('nested/', page_size=2) == sorted(keys)


def test_identical_reput(r2_run):
    # Plain S3 PUT has no exclusive-create guarantee; only identical re-put is checked.
    payload = secrets.token_bytes(64)
    r2_run.put('reput/object', payload)
    r2_run.put('reput/object', payload)
    assert r2_run.get('reput/object') == payload


def test_encrypted_events(r2_run, taxonomy):
    account, registry, _, agents, work, _, phone = taxonomy
    a = next(s for s in work.scopes.values() if s.name == 'client-a')
    b = next(s for s in work.scopes.values() if s.name == 'client-b')
    read_key, sibling = work.keys[a.scope_id], work.keys[b.scope_id]
    paths = []
    bodies = [f'R2 private memory {i} {secrets.token_hex(16)}' for i in range(3)]
    tags, task = ['R2 private tag'], 'R2 private task'
    for index, body in enumerate(bodies):
        agent, write_key = phone if index == 2 else (agents[0], read_key.write_key)
        data = seal_event(write_key, agent, body, label=Label.secret, tags=tags, task=task)
        path = event_path(verify_event(data, registry))
        r2_run.put(path, data)
        paths.append(path)
    prefix = f'events/{work.root.profile_id}/{a.scope_id}/'
    assert r2_run.list(prefix) == sorted(paths)
    for path, body in zip(paths, bodies):
        raw = r2_run.get(path)
        assert event_path(verify_event(raw, registry)) == path
        payload = open_event(raw, registry, read_key)
        assert payload['body'] == body
        assert payload['tags'] == tags and payload['task'] == task and payload['label'] == 'secret'
        with pytest.raises(LogError): open_event(raw, registry, sibling)
        with pytest.raises(LogError): open_event(raw, registry, phone[1])
        for hidden in (body, *tags, task, 'secret', account.email, account.email.lower()):
            assert hidden.encode() not in raw, 'private metadata found in stored envelope'
        r2_run.events += 1
