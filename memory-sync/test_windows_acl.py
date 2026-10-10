"""Portable ACL policy tests, NOT evidence of native Windows execution."""
import re
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest
import windows_acl as acl
import signed_packets as signed
import security_wizard as wizard
from test_signed_packets import secure_peers
from test_security_wizard import verified
from test_ota_update import installed


def test_acl_dependency_is_shipped_in_private_worker_and_stable_consumer(installed):
    local, _, _, _ = installed
    expected = Path(acl.__file__).read_bytes()
    assert (local / 'versions' / '0' / 'windows_acl.py').read_bytes() == expected
    assert (local / 'consumer' / 'windows_acl.py').read_bytes() == expected
    import build_package
    assert 'windows_acl.py' in build_package.FILES

USER = 'S-1-5-21-111111111-222222222-333333333-1001'
# Valid SDDL fixtures: the numeric reports are the RawSecurityDescriptor projection.
FILE_SDDL = f'O:{USER}G:SYD:P(A;;FA;;;{USER})(A;;FA;;;SY)'
DIRECTORY_SDDL = f'O:{USER}G:SYD:P(A;OICI;FA;;;{USER})(A;OICI;FA;;;SY)'


def report(directory=False):
    # Decode only these explicit fixture strings, not arbitrary/native ACL output.
    # This guards fixture/report consistency without a production SDDL parser.
    sddl = DIRECTORY_SDDL if directory else FILE_SDDL
    match = re.fullmatch(r'O:(S-1-5-21-[0-9-]+)G:SYD:P((?:\(A;(?:OICI)?;FA;;;(?:S-1-5-21-[0-9-]+|SY)\))+)', sddl)
    assert match is not None
    entries = re.findall(r'\(A;(OICI)?;FA;;;([^()]+)\)', match[2])
    return {'sid': USER, 'owner': match[1], 'control': 0x9004, 'directory': directory,
            'rules': [{'sid': acl.SYSTEM if sid == 'SY' else sid, 'type': 0,
                       'flags': 3 if flags == 'OICI' else 0, 'mask': 0x1f01ff}
                      for flags, sid in entries]}


@pytest.mark.parametrize('directory', [False, True])
def test_restrictive_sddl_policy_fixture(directory):
    value = report(directory)
    assert acl.validate(value, directory=directory) == value
    # Values above correspond to the documented FA mask, P flag, OI/CI and SID aliases.
    assert (DIRECTORY_SDDL if directory else FILE_SDDL).count('(A;') == 2


@pytest.mark.parametrize('principal', ['S-1-1-0', 'S-1-5-32-545', 'S-1-5-11',
                                         'S-1-5-32-544', 'S-1-5-21-111111111-222222222-333333333-2001'])
@pytest.mark.parametrize('inherited', [False, True])
@pytest.mark.parametrize('rights', [0x120089, 0x120116])
def test_every_unapproved_read_or_write_principal_rejected(principal, inherited, rights):
    # SDDL example appended ACE: (A;ID;0x120089;;;AU), or explicit equivalents.
    value = report(True)
    value['rules'].append({'sid': principal, 'type': 0, 'flags': 16 if inherited else 0, 'mask': rights})
    with pytest.raises(ValueError): acl.validate(value, directory=True)


@pytest.mark.parametrize('mutation', ['owner', 'unprotected', 'null', 'inherited-user', 'deny', 'object', 'inherit-only', 'missing-full', 'unknown-rights', 'type'])
def test_unsupported_or_unverifiable_acl_is_closed(mutation):
    value = report()
    if mutation == 'owner': value['owner'] = acl.SYSTEM
    elif mutation == 'unprotected': value['control'] = 4
    elif mutation == 'null': value['rules'] = []
    elif mutation == 'inherited-user': value['rules'][0]['flags'] = 16
    elif mutation == 'deny': value['rules'][0]['type'] = 1
    elif mutation == 'object': value['rules'][0]['type'] = 5
    elif mutation == 'inherit-only': value['rules'][0]['flags'] = 8
    elif mutation == 'missing-full': value['rules'][0]['mask'] = 0x120089
    elif mutation == 'unknown-rights': value['rules'][0]['mask'] = 0x10000000
    elif mutation == 'type': value['directory'] = 'false'
    with pytest.raises(ValueError): acl.validate(value, directory=False)


def test_native_adapter_requires_real_windows():
    if acl.os.name == 'nt': pytest.skip('portable negative host test')
    with pytest.raises(ValueError, match='requires Windows'): acl.apply(Path(__file__))


def test_native_adapter_treats_path_as_data(monkeypatch, tmp_path):
    # Patch only the adapter's OS boundary, not global os.name / pathlib behavior.
    monkeypatch.setattr(acl, 'os', SimpleNamespace(name='nt'))
    path = tmp_path / "apostrophe' ; $path [literal].json"
    path.write_text('public test data')
    calls = []
    def native(target, provision):
        calls.append((target, provision))
        return report()
    monkeypatch.setattr(acl, 'native_report', native)
    acl.apply(path, provision=True)
    assert calls == [(str(path), True)]
    assert not hasattr(acl, 'subprocess')


@pytest.mark.parametrize('failure', ['os', 'decode', 'broad-acl'])
def test_native_adapter_failure_is_not_fallback(monkeypatch, tmp_path, failure):
    monkeypatch.setattr(acl, 'os', SimpleNamespace(name='nt'))
    def native(*args):
        if failure == 'os': raise OSError('not available')
        if failure == 'decode': return {**report(), 'rules': acl.parse_acl(b'\x02\x00\x08\x00\x01\x00\x00\x00')}
        value = report()
        value['rules'].append({'sid': 'S-1-1-0', 'type': 0, 'flags': 0, 'mask': 0x120089})
        return value
    monkeypatch.setattr(acl, 'native_report', native)
    with pytest.raises(ValueError): acl.apply(tmp_path / 'file')


def sid_bytes(sid):
    parts = [int(part) for part in sid.split('-')[2:]]
    return bytes([1, len(parts) - 1]) + parts[0].to_bytes(6, 'big') + struct.pack('<%dI' % (len(parts) - 1), *parts[1:])


def ace_bytes(sid, kind=0, flags=0, mask=0x1f01ff):
    body = struct.pack('<I', mask) + sid_bytes(sid)
    return struct.pack('<BBH', kind, flags, 4 + len(body)) + body


def acl_bytes(*aces, revision=2):
    body = b''.join(aces)
    return struct.pack('<BBHHH', revision, 0, 8 + len(body), len(aces), 0) + body


@pytest.mark.parametrize('sid', [USER, acl.SYSTEM, 'S-1-1-0', 'S-1-5-32-545'])
def test_sid_decoder_round_trips_binary_form(sid):
    data = sid_bytes(sid)
    assert acl.parse_sid(data + b'trailing') == (sid, len(data))


@pytest.mark.parametrize('directory', [False, True])
def test_acl_decoder_matches_restrictive_policy_report(directory):
    flags = 3 if directory else 0
    rules = acl.parse_acl(acl_bytes(ace_bytes(USER, flags=flags), ace_bytes(acl.SYSTEM, flags=flags)))
    assert rules == report(directory)['rules']
    value = {**report(directory), 'rules': rules}
    assert acl.validate(value, directory=directory) == value


def test_acl_decoder_keeps_signed_mask_projection():
    assert acl.parse_acl(acl_bytes(ace_bytes(USER, mask=0x80000000)))[0]['mask'] == -0x80000000


@pytest.mark.parametrize('data', [
    b'', acl_bytes(revision=1), acl_bytes(ace_bytes(USER))[:-1],
    acl_bytes(ace_bytes(USER, kind=5)), acl_bytes(ace_bytes(USER, kind=9)),
    struct.pack('<BBHHH', 2, 0, 12, 1, 0) + struct.pack('<BBH', 0, 0, 4),
    struct.pack('<BBHHH', 2, 0, 8, 1, 0),
    acl_bytes(ace_bytes(USER)[:8] + b'\x02' + ace_bytes(USER)[9:]),
])
def test_malformed_or_unsupported_acl_is_closed(data):
    with pytest.raises(ValueError): acl.parse_acl(data)


def test_provisioning_descriptor_grants_only_user_and_system():
    assert acl.provision_sddl(USER, False) == f'O:{USER}D:P(A;;FA;;;{USER})(A;;FA;;;SY)'
    assert acl.provision_sddl(USER, True) == f'O:{USER}D:P(A;OICI;FA;;;{USER})(A;OICI;FA;;;SY)'
    for sid in ('S-1-1-0', USER + ')(A;;FA;;;WD', None):
        with pytest.raises(ValueError): acl.provision_sddl(sid, False)


@pytest.mark.parametrize('target', ['directory', 'identity.json', 'trust.json'])
def test_acl_gate_blocks_private_key_use_and_activation(secure_peers, monkeypatch, target):
    first, second = verified(secure_peers)
    _, _, _, sa, sb = secure_peers
    def check(path, *, provision=False):
        path = Path(path)
        if path == sa.directory and target == 'directory' or path == sa.directory / target:
            bad = report(path.is_dir())
            bad['rules'].append({'sid': 'S-1-5-11', 'type': 0, 'flags': 16, 'mask': 0x120089})
            acl.validate(bad, directory=path.is_dir())
        return report(path.is_dir())
    monkeypatch.setattr(signed, 'windows_private', check)
    with pytest.raises(ValueError): signed.Security(sa.directory)
    with pytest.raises(ValueError): sa.sign({'group': sa.public['group'], 'node': 'mac'})
    with pytest.raises(ValueError): wizard.resume(**first, activate=True, confirm_both_peers=True, confirm_legacy_boundary=True)
    with wizard.sync.connect(first['database']) as c:
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='_sync_security'").fetchall()


def test_unsafe_trust_is_rejected_before_private_key_material_is_loaded(secure_peers, monkeypatch):
    _, _, _, sa, sb = secure_peers
    Private, Public, InvalidSignature = signed.crypto()
    class NoKeyLoad:
        @staticmethod
        def from_private_bytes(value):
            raise AssertionError('unsafe trust must be rejected before key loading')
    monkeypatch.setattr(signed, 'crypto', lambda: (NoKeyLoad, Public, InvalidSignature))
    def check(path, *, provision=False):
        if Path(path) == sa.directory / 'trust.json': raise ValueError('unsafe ACL')
    monkeypatch.setattr(signed, 'windows_private', check)
    with pytest.raises(ValueError, match='unsafe ACL'): signed.Security(sa.directory)


def test_failed_file_acl_provisioning_never_writes_secret_bytes(tmp_path, monkeypatch):
    def check(path, *, provision=False): raise ValueError('ACL failed')
    def wire(value): raise AssertionError('must not serialize private data before ACL verification')
    monkeypatch.setattr(signed, 'windows_private', check)
    monkeypatch.setattr(signed, 'wire', wire)
    with pytest.raises(ValueError, match='ACL failed'):
        signed.write_local(tmp_path / 'identity.json', {'private_key': 'synthetic'})
    assert list(tmp_path.iterdir()) == []


def test_identity_acl_failure_happens_before_key_generation(tmp_path, monkeypatch):
    calls = []
    def check(path, *, provision=False):
        calls.append((Path(path), provision))
        raise ValueError('ACL unsupported')
    monkeypatch.setattr(signed, 'windows_private', check)
    directory = tmp_path / 'keys'
    with pytest.raises(ValueError): signed.init_identity(directory, '11111111-1111-1111-1111-111111111111', 'windows')
    assert calls == [(directory, True)]
    assert list(directory.iterdir()) == []
