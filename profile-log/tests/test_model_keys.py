import copy
import pickle
import pytest
from agentmesh_log.encoding import wire
from agentmesh_log.errors import LogError, LabelLoweringRequiresConfirmation
from agentmesh_log.model import Account, Profile, ScopeTree, Label, resolve_label, change_label
from agentmesh_log.keys import ScopeReadKey, ScopeWriteKey, wrap_key, unwrap_key


@pytest.fixture
def tree():
    tree = ScopeTree(Profile('work'))
    tree.add(tree.root.scope_id, 'common', 'common')
    a = tree.add(tree.root.scope_id, 'project', 'client-a')
    tree.add(tree.root.scope_id, 'project', 'client-b')
    tree.add(a.scope_id, 'project', 'nested')
    return tree


def test_account_profile_ids_and_privacy():
    a = Account('Private.Email@Example.test')
    b = Account(a.email)
    assert a.account_id != b.account_id
    record = a.public_record()
    assert set(record) == {'format', 'account_id', 'root_public_key'}
    assert a.email.encode() not in wire(record)
    assert a.email.lower().encode() not in wire(record)
    p = Profile('work')
    assert p.profile_id == p.scope_id and p.parent is None and p.kind == 'profile'
    assert len(p.profile_id) == 32


def test_labels():
    assert Label.general < Label.internal < Label.secret
    assert resolve_label(Label.internal, Label.general, Label.secret) == Label.secret
    assert resolve_label(Label.general) == Label.general
    assert change_label(Label.general, Label.secret) == Label.secret
    with pytest.raises(LabelLoweringRequiresConfirmation): change_label(Label.secret, Label.internal)
    assert change_label(Label.secret, Label.general, user_confirmed=True) == Label.general


def test_tree_descendants_and_independent_keys(tree):
    root = tree.keys[tree.root.scope_id]
    recovered = {tree.root.scope_id: root}
    for scope in tree.scopes.values():
        if scope.parent is None: continue
        read = unwrap_key(tree.wraps[scope.scope_id], recovered[scope.parent])
        assert read.key_id == tree.keys[scope.scope_id].key_id
        recovered[scope.scope_id] = read
    assert len({k.key_id for k in tree.keys.values()}) == len(tree.keys)
    children = list(tree.wraps)
    a, b = tree.keys[children[1]], tree.keys[children[2]]
    for record in (tree.wraps[a.scope_id], tree.wraps[b.scope_id]):
        with pytest.raises(LogError): unwrap_key(record, a)


@pytest.mark.parametrize('field', ['format', 'profile', 'parent_scope', 'parent_key_id', 'child_scope', 'child_key_id', 'ciphertext', 'auth'])
def test_wrap_tamper(tree, field):
    record = copy.deepcopy(next(iter(tree.wraps.values())))
    record[field] = ('0' if record[field][0] != '0' else '1') + record[field][1:]
    with pytest.raises(LogError): unwrap_key(record, tree.keys[tree.root.scope_id])


def test_unwrapped_key_id_checked(tree):
    from agentmesh_log.keys import encrypt, WRAP_DOMAIN
    from agentmesh_log.encoding import typed
    record = copy.deepcopy(next(iter(tree.wraps.values())))
    record['child_key_id'] = '0' * 64
    header = {k: record[k] for k in ('profile', 'parent_scope', 'parent_key_id', 'child_scope', 'child_key_id')}
    child = tree.keys[record['child_scope']]
    parent = tree.keys[tree.root.scope_id]
    record['ciphertext'] = encrypt(child._private.private_bytes_raw(), parent.write_key, WRAP_DOMAIN + typed(header))
    record['auth'] = wrap_auth(record, parent)
    with pytest.raises(LogError, match='wrapped key mismatch'): unwrap_key(record, parent)


def test_single_branch_rotation(tree):
    a = next(s for s in tree.scopes.values() if s.name == 'client-a')
    b = next(s for s in tree.scopes.values() if s.name == 'client-b')
    nested = next(s for s in tree.scopes.values() if s.name == 'nested')
    old_a = tree.keys[a.scope_id]
    b_bytes = wire(tree.wraps[b.scope_id])
    nested_key_id = tree.keys[nested.scope_id].key_id
    tree.rotate(a.scope_id)
    assert tree.keys[a.scope_id].key_id != old_a.key_id
    assert tree.epochs[a.scope_id][0] is old_a
    assert wire(tree.wraps[b.scope_id]) == b_bytes
    assert tree.keys[nested.scope_id].key_id == nested_key_id
    assert unwrap_key(tree.wraps[nested.scope_id], tree.keys[a.scope_id]).key_id == nested_key_id
    with pytest.raises(LogError): unwrap_key(tree.wraps[nested.scope_id], old_a)


def test_write_key_public_only(tree):
    read = tree.keys[tree.root.scope_id]
    write = read.write_key
    assert isinstance(write, ScopeWriteKey)
    assert not hasattr(write, '__dict__')
    assert set(write.__slots__) == {'profile_id', 'scope_id', 'public_bytes'}
    assert not hasattr(write, 'decrypt') and not hasattr(write, '_private')
    serialized = pickle.dumps(write)
    assert read._private.private_bytes_raw() not in serialized
    assert pickle.loads(serialized) == write
    with pytest.raises(LogError): unwrap_key(next(iter(tree.wraps.values())), write)


def test_invalid_tree_operations(tree):
    for kind in ('task', 'tag', 'profile'):
        with pytest.raises(LogError): tree.add(tree.root.scope_id, kind, 'invalid')
    with pytest.raises(LogError): tree.add('0'*32, 'project', 'orphan')
    with pytest.raises(LogError): tree.rotate('0'*32)
    other = ScopeTree(Profile('personal'))
    with pytest.raises(LogError): wrap_key(tree.keys[tree.root.scope_id], other.keys[other.root.scope_id])


def wrap_auth(record, parent):
    """Independent wire-contract oracle for authenticated wrap fixtures."""
    import hashlib
    import hmac
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from agentmesh_log.encoding import typed
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
               info=b'AgentMesh/scope-wrap-auth-key/v1\x00' + bytes.fromhex(parent.key_id)).derive(
                   parent._private.private_bytes_raw())
    unsigned = {k: v for k, v in record.items() if k != 'auth'}
    return hmac.new(key, b'AgentMesh/scope-wrap-auth/v1\x00' + typed(unsigned), hashlib.sha256).hexdigest()


def test_write_only_wrap_forgery_rejected(tree):
    from agentmesh_log.keys import encrypt, WRAP_DOMAIN
    from agentmesh_log.encoding import typed
    parent = tree.keys[tree.root.scope_id]
    public = parent.write_key
    child_id = next(iter(tree.wraps))
    attacker = ScopeReadKey.generate(public.profile_id, child_id)
    header = dict(profile=public.profile_id, parent_scope=public.scope_id,
                  parent_key_id=public.key_id, child_scope=child_id, child_key_id=attacker.key_id)
    forged = dict(format='agentmesh-scope-wrap-v1', **header,
                  ciphertext=encrypt(attacker._private.private_bytes_raw(), public, WRAP_DOMAIN + typed(header)))
    with pytest.raises(LogError): unwrap_key(forged, parent)
    forged['auth'] = '0' * 64
    with pytest.raises(LogError): unwrap_key(forged, parent)
    with pytest.raises(LogError): wrap_key(attacker, public)


def test_wrap_auth_contract_and_before_decryption(tree, monkeypatch):
    import agentmesh_log.keys as keys
    parent = tree.keys[tree.root.scope_id]
    record = copy.deepcopy(next(iter(tree.wraps.values())))
    assert record['auth'] == wrap_auth(record, parent)
    assert unwrap_key(record, parent).key_id == record['child_key_id']
    record['auth'] = ('0' if record['auth'][0] != '0' else '1') + record['auth'][1:]
    def forbidden(*args): pytest.fail('unauthenticated wrap reached decryption')
    monkeypatch.setattr(keys, 'decrypt', forbidden)
    with pytest.raises(LogError): unwrap_key(record, parent)
