"""Local account and scope taxonomy. Names and email are never public identifiers."""
from dataclasses import dataclass, field
from enum import IntEnum
import secrets
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from .encoding import hex_bytes
from .errors import ValidationError, LabelLoweringRequiresConfirmation


def new_id():
    return secrets.token_hex(16)


class Label(IntEnum):
    general = 0
    internal = 1
    secret = 2


def _label(value):
    if not isinstance(value, Label): raise ValidationError('invalid label')
    return value


def resolve_label(scope_default, detected=None, agent=None):
    return max(_label(v) for v in (scope_default, detected, agent) if v is not None)


def change_label(current, new, *, user_confirmed=False):
    if _label(new) < _label(current) and user_confirmed is not True:
        raise LabelLoweringRequiresConfirmation('label lowering requires user confirmation')
    return new


class Account:
    def __init__(self, email=None):
        self.email = email
        self.account_id = new_id()
        self._root = Ed25519PrivateKey.generate()

    @classmethod
    def from_private(cls, account_id, raw):
        hex_bytes(account_id, 16)
        if type(raw) is not bytes or len(raw) != 32: raise ValidationError('invalid private key')
        obj = cls.__new__(cls)
        obj.account_id, obj.email = account_id, None
        obj._root = Ed25519PrivateKey.from_private_bytes(raw)
        return obj

    @classmethod
    def from_public(cls, record):
        from .identity import Registry
        Registry(record)
        obj=cls.__new__(cls); obj.account_id=record['account_id']; obj.email=None
        obj._root=None; obj._public=dict(record)
        return obj

    def private_bytes(self):
        if self._root is None: raise ValueError('account root unavailable')
        return self._root.private_bytes_raw()

    def public_record(self):
        if self._root is None: return dict(self._public)
        return {'format': 'agentmesh-account-v1', 'account_id': self.account_id,
                'root_public_key': self._root.public_key().public_bytes_raw().hex()}


@dataclass(frozen=True)
class Scope:
    profile_id: str
    parent: str | None
    kind: str
    name: str
    default_label: Label = Label.general
    scope_id: str = field(default_factory=new_id)

    def __post_init__(self):
        hex_bytes(self.profile_id, 16); hex_bytes(self.scope_id, 16)
        if self.parent is not None: hex_bytes(self.parent, 16)
        if self.kind not in ('profile', 'common', 'project') or type(self.name) is not str:
            raise ValidationError('invalid scope')
        if (self.kind == 'profile') != (self.parent is None): raise ValidationError('invalid scope parent')
        if self.kind == 'profile' and self.profile_id != self.scope_id: raise ValidationError('invalid profile root')
        _label(self.default_label)


class Profile(Scope):
    def __init__(self, name, default_label=Label.general):
        identifier = new_id()
        super().__init__(identifier, None, 'profile', name, default_label, identifier)


class ScopeTree:
    def __init__(self, profile):
        from .keys import ScopeReadKey
        if not isinstance(profile, Profile): raise ValidationError('profile root required')
        self.root = profile
        self.scopes = {profile.scope_id: profile}
        key = ScopeReadKey.generate(profile.profile_id, profile.scope_id)
        self.keys = {profile.scope_id: key}
        self.epochs = {profile.scope_id: [key]}
        self.wraps = {}

    def add(self, parent, kind, name, default_label=Label.general, *, scope_id=None):
        from .keys import ScopeReadKey, wrap_key
        if parent not in self.scopes or kind not in ('common', 'project'):
            raise ValidationError('invalid scope parent or kind')
        if type(name) is not str or not name or '/' in name:
            raise ValidationError('invalid scope name')
        if any(s.parent == parent and s.name == name for s in self.scopes.values()):
            raise ValidationError('duplicate scope name')
        if scope_id in self.scopes: raise ValidationError('duplicate scope id')
        scope = Scope(self.root.profile_id, parent, kind, name, default_label, **({'scope_id':scope_id} if scope_id is not None else {}))
        key = ScopeReadKey.generate(scope.profile_id, scope.scope_id)
        record = wrap_key(key, self.keys[parent])
        self.scopes[scope.scope_id] = scope
        self.keys[scope.scope_id] = key
        self.epochs[scope.scope_id] = [key]
        self.wraps[scope.scope_id] = record
        return scope

    def rotate(self, scope_id):
        from .keys import ScopeReadKey, wrap_key
        if scope_id not in self.scopes: raise ValidationError('unknown scope')
        scope = self.scopes[scope_id]
        key = ScopeReadKey.generate(scope.profile_id, scope_id)
        wraps = {}
        if scope.parent is not None: wraps[scope_id] = wrap_key(key, self.keys[scope.parent])
        for child in self.scopes.values():
            if child.parent == scope_id: wraps[child.scope_id] = wrap_key(self.keys[child.scope_id], key)
        self.keys[scope_id] = key
        self.epochs[scope_id].append(key)
        self.wraps.update(wraps)
        return key
