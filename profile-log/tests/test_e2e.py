import pytest
from agentmesh_log.encoding import parse, wire
from agentmesh_log.errors import LogError
from agentmesh_log.events import seal_event, verify_event, open_event, event_path, wrap_path
from agentmesh_log.keys import unwrap_key, ScopeWriteKey
from agentmesh_log.model import Label
from test_storage import backend


def test_taxonomy_write_only_end_to_end(backend, taxonomy):
    account, registry, devices, agents, work, personal, phone = taxonomy
    all_objects = []
    records = [('identity/account.json', account.public_record())]
    records += [(f'identity/devices/{d.device_id}.json', c) for d, c in devices]
    records += [(f'identity/agents/{a.agent_id}.json', a.certificate()) for a in agents]
    for tree in (work, personal):
        records += [(wrap_path(r), r) for r in tree.wraps.values()]
    for path, record in records:
        data = wire(record); backend.put(path, data); all_objects.append(data)

    expected = {}
    for tree in (work, personal):
        for scope in tree.scopes.values():
            is_phone = scope.name == 'client-a'
            writer, key = phone if is_phone else (agents[0], tree.keys[scope.scope_id].write_key)
            data = seal_event(key, writer, {'memory': f'private body for {scope.name}'},
                              label=Label.secret, tags=['private tag'], task='private task')
            envelope = verify_event(data, registry)
            path = event_path(envelope); expected[path] = (scope, tree)
            backend.put(path, data); all_objects.append(data)

    assert isinstance(phone[1], ScopeWriteKey)
    assert set(phone[1].__slots__) == {'scope_id', 'profile_id', 'public_bytes'}
    for tree in (work, personal):
        prefix = f'events/{tree.root.profile_id}/'
        paths = list(backend.list(prefix))
        assert paths == sorted(p for p, (_, t) in expected.items() if t is tree)
        recovered = {tree.root.scope_id: tree.keys[tree.root.scope_id]}
        for scope in tree.scopes.values():
            if scope.parent is not None:
                record = tree.wraps[scope.scope_id]
                stored = parse(backend.get(wrap_path(record)))
                recovered[scope.scope_id] = unwrap_key(stored, recovered[scope.parent])
        for path in paths:
            data = backend.get(path); scope, _ = expected[path]
            verified = verify_event(data, registry)
            assert event_path(verified) == path
            payload = open_event(data, registry, recovered[scope.scope_id])
            assert payload['body'] == {'memory': f'private body for {scope.name}'}
            assert payload['label'] == 'secret'
            assert payload['task'] == 'private task' and payload['tags'] == ['private tag']
            for other_tree in (work, personal):
                for other in other_tree.keys.values():
                    if other.scope_id != scope.scope_id:
                        with pytest.raises(LogError): open_event(data, registry, other)
            with pytest.raises(LogError): open_event(data, registry, phone[1])

    assert len(list(backend.list('events/'))) == 6
    stored_bytes = b''.join(backend.get(path) for path in backend.list(''))
    for secret in (account.email, account.email.lower(), 'private body', 'private tag', 'private task',
                   '"secret"', '"work"', '"personal"', '"common"', '"client-a"', '"client-b"'):
        assert secret.encode() not in stored_bytes
