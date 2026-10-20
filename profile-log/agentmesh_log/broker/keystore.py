"""Owner-only Phase 3 state. Same-user hostile code is outside this boundary."""
from contextlib import contextmanager
from dataclasses import dataclass, asdict, field
import hashlib
import os
from pathlib import Path
import secrets
import stat
import tempfile
from typing import Protocol
from ..encoding import wire, parse, hex_bytes, fields
from ..model import Account, Profile, Scope, ScopeTree, Label
from ..identity import DeviceIdentity, Registry
from ..keys import ScopeReadKey
from ..storage.filesystem import _check
from .grants import Grant


def atomic_write(path, data):
    path = Path(path); _check(path)
    fd, temp = tempfile.mkstemp(suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            if os.name != 'nt': os.fchmod(stream.fileno(), 0o600)
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        _check(path); os.replace(temp, path)
        if os.name != 'nt':
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(fd)
            finally: os.close(fd)
    finally:
        if os.path.exists(temp): os.unlink(temp)


class BrokerHome:
    def __init__(self, root):
        self.root = Path(os.path.abspath(root))
        for path in reversed((self.root, *self.root.parents)): _check(path)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.check()
        for name in ('agents', 'replica', 'index'):
            path = self.root / name; _check(path); path.mkdir(mode=0o700, exist_ok=True)
            if os.name != 'nt' and stat.S_IMODE(path.stat().st_mode) & 0o077:
                raise ValueError('unsafe broker directory')

    def check(self):
        _check(self.root)
        info = self.root.stat()
        if os.name != 'nt' and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
            raise ValueError('unsafe broker home')

    def address(self, device_id):
        return r'\\.\pipe\agentmesh-broker-' + device_id[:16] if os.name == 'nt' else str(self.root / 'broker.sock')

    @contextmanager
    def lock(self):
        path = self.root / '.lock'; _check(path)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(str(os.getpid()).encode()); stream.flush(); os.fsync(stream.fileno())
            yield
        finally: path.unlink()


@dataclass
class BrokerState:
    account: Account
    device: DeviceIdentity
    certificates: list
    trees: list
    grants: dict
    index_master_key: bytes
    audit_key: bytes
    control: dict = field(default_factory=dict)

    @classmethod
    def create(cls, profiles):
        if not profiles or len(set(profiles)) != len(profiles): raise ValueError('invalid profiles')
        if any(type(p) is not str or not p or '/' in p for p in profiles): raise ValueError('invalid profiles')
        account, device = Account(), DeviceIdentity()
        trees = [ScopeTree(Profile(p)) for p in profiles]
        for tree in trees: tree.add(tree.root.scope_id, 'common', 'common')
        return cls(account, device, [device.certificate(account)], trees, {}, secrets.token_bytes(32), secrets.token_bytes(32))

    def add(self, name, default_label=Label.general):
        from .grants import resolve
        parent_name,sep,local=name.rpartition('/')
        if not sep or not local: raise ValueError('invalid scope name')
        parent=resolve(self,parent_name)
        tree=next(t for t in self.trees if parent in t.scopes)
        return tree.add(parent,'project',local,default_label)

    def registry(self):
        registry = Registry(self.account.public_record())
        if self.control.get('registry_records'):
            from ..registry_store import RegistryStore
            records=self.control['registry_records']
            class Cached:
                def list(self,prefix): return sorted(k for k in records if k.startswith(prefix))
                def get(self,key): return wire(records[key])
            return RegistryStore(Cached(),self.account.public_record()).load()
        for cert in self.certificates: registry.add_device(cert)
        return registry

    def revoke(self, agent_id):
        self.grants.pop(agent_id, None)

    def record(self):
        trees = []
        for tree in self.trees:
            trees.append({'root': tree.root.scope_id,
                'scopes': [{**asdict(s), 'default_label': s.default_label.name} for s in tree.scopes.values()],
                'epochs': {sid: [k.private_bytes().hex() for k in epochs] for sid, epochs in tree.epochs.items()},
                'wraps': tree.wraps})
        result = {'format': 'agentmesh-broker-state-v1', 'account': self.account.public_record(),
            'account_private_key': self.account.private_bytes().hex() if self.account._root is not None else None, 'device_id': self.device.device_id,
            'device_private_key': self.device.private_bytes().hex(), 'certificates': self.certificates,
            'trees': trees, 'grants': {aid: asdict(g) for aid, g in self.grants.items()},
            'index_master_key': self.index_master_key.hex(), 'audit_key': self.audit_key.hex()}
        if self.control:
            result['format']='agentmesh-broker-state-v2'; result['control']=self.control
        return result

    @classmethod
    def from_record(cls, record):
        control = {}
        if record.get('format') == 'agentmesh-broker-state-v2':
            record=dict(record); control=record.pop('control'); record['format']='agentmesh-broker-state-v1'
        fields(record, {'format', 'account', 'account_private_key', 'device_id', 'device_private_key',
            'certificates', 'trees', 'grants', 'index_master_key', 'audit_key'}, 'agentmesh-broker-state-v1')
        account = Account.from_public(record['account']) if record['account_private_key'] is None else Account.from_private(record['account']['account_id'], hex_bytes(record['account_private_key'], 32))
        if account.public_record() != record['account']: raise ValueError('invalid account binding')
        device = DeviceIdentity.from_private(record['device_id'], hex_bytes(record['device_private_key'], 32))
        trees = []
        for value in record['trees']:
            scopes = {s['scope_id']: Scope(**{**s, 'default_label': Label[s['default_label']]}) for s in value['scopes']}
            root = scopes[value['root']]
            profile = Profile.__new__(Profile)
            for k, v in asdict(root).items(): object.__setattr__(profile, k, v)
            scopes[root.scope_id] = profile
            tree = ScopeTree.__new__(ScopeTree); tree.root, tree.scopes = profile, scopes
            tree.epochs = {sid: [ScopeReadKey.from_private(root.profile_id, sid, hex_bytes(k, 32)) for k in epochs]
                for sid, epochs in value['epochs'].items()}
            tree.keys = {sid: epochs[-1] for sid, epochs in tree.epochs.items() if epochs}; tree.wraps = value['wraps']
            trees.append(tree)
        state = cls(account, device, record['certificates'], trees,
            {aid: Grant(**g) for aid, g in record['grants'].items()},
            hex_bytes(record['index_master_key'], 32), hex_bytes(record['audit_key'], 32))
        state.control=control
        from .grants import names
        names(state)  # Reject ambiguous imported local names before use.
        registry = state.registry()
        if registry._devices.get(device.device_id) != device.public_bytes: raise ValueError('invalid device binding')
        return state


class KeyStore(Protocol):
    def load(self) -> BrokerState: ...
    def save(self, state: BrokerState): ...


class FileKeyStore:
    def __init__(self, home):
        self.home, self.path = home, home.root / 'keystore.json'

    def _read(self):
        self.home.check(); _check(self.path)
        fd = os.open(self.path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode): raise ValueError('invalid key store')
            if os.name != 'nt' and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600):
                raise ValueError('unsafe key store')
            return info.st_mtime_ns, stream.read(1024 * 1024 + 1)

    def fingerprint(self):
        mtime, data = self._read()
        return mtime, hashlib.sha256(data).hexdigest()

    def load(self):
        return BrokerState.from_record(parse(self._read()[1]))

    def save(self, state):
        self.home.check(); atomic_write(self.path, wire(state.record()))
