"""Several accounts in one workspace (team workspace W2): a registry over pinned foreign accounts.

Trust in another account's root key comes from exactly two places:

- a **join pin**: this device's owner checked the workspace owner's fingerprint out of band when joining
  (``state.control['federation_pins'][profile] = {'owner': account, 'root': hex}``);
- a **workspace member entry** carrying ``account_root``, signed by an owner or admin already trusted.

Each trusted account's devices, revocations and recovery records are read by that account's own
``RegistryStore`` (account-pinned, so one account can never certify another's devices). ``Federation`` puts
them behind the ``Registry`` interface and adds the workspace rules:

- ``profile_owner(profile)``: the account that owns a profile (this account unless the profile is pinned).
- ``accepts(account, profile, at)``: an event author is the profile owner, or held a writing role then.
- ``may_hold(profile, account, at)``: a key recipient is the profile owner, or held a reading role then.

Without workspace entries or pins, ``load`` returns the plain single-account ``Registry`` unchanged.
"""
import hashlib
from .encoding import fields, hex_bytes, parse, wire
from .identity import Registry
from .errors import IdentityError

ACCOUNTS = 'registry/accounts/'
WRITERS = ('owner', 'admin', 'member')
HOLDERS = ('owner', 'admin', 'member')


def account_path(account_id):
    hex_bytes(account_id, 16)
    return ACCOUNTS + account_id + '.json'


def fingerprint(root_hex):
    """A short, comparable digest of an account root key, for out-of-band checks (QR, call, chat)."""
    digest = hashlib.sha256(b'AgentMesh/account-fingerprint/v1\x00' + hex_bytes(root_hex, 32)).hexdigest()[:32]
    return '-'.join(digest[i:i + 4] for i in range(0, 32, 4))


def account_record(account_id, root_hex):
    return {'format': 'agentmesh-account-v1', 'account_id': account_id, 'root_public_key': root_hex}


def read_account(storage, account_id):
    """The published account record of ``account_id`` (unauthenticated until a pin or fingerprint matches)."""
    record = parse(storage.get(account_path(account_id)))
    fields(record, {'format', 'account_id', 'root_public_key'}, 'agentmesh-account-v1')
    if record['account_id'] != account_id: raise ValueError('account mismatch')
    hex_bytes(record['root_public_key'], 32)
    return record


def publish_account(storage, record):
    path = account_path(record['account_id'])
    if path in set(storage.list(path)):
        if parse(storage.get(path)) != record: raise ValueError('account record conflict')
        return
    storage.put(path, wire(record))


class Federation:
    """Several account registries behind the Registry interface; this device's own account first."""

    def __init__(self, own, foreign, owners, workspaces):
        self.own, self.foreign, self.owners, self.workspaces = own, foreign, owners, workspaces
        self.account_id = own.account_id
        self.recoveries = own.recoveries
        self.certificates, self.device_records, self.revocations, self._devices = {}, {}, {}, {}
        self.device_classes = {}
        for registry in [own, *foreign.values()]:
            self._devices.update(registry._devices)
            self.device_classes.update(getattr(registry, 'device_classes', {}))
            self.certificates.update(registry.certificates)
            self.device_records.update(registry.device_records)
            self.revocations.update(registry.revocations)

    def _registry(self, account):
        if account == self.own.account_id: return self.own
        if account in self.foreign: return self.foreign[account]
        raise IdentityError('unknown account')

    def _device_registry(self, device_id):
        cert = self.certificates.get(device_id)
        if cert is None: raise IdentityError('unknown device')
        return self._registry(cert['account_id'])

    def device_public(self, device_id, *, at=None, seen=None):
        return self._device_registry(device_id).device_public(device_id, at=at, seen=seen)

    def verify_agent(self, cert): return self._registry(cert.get('account_id')).verify_agent(cert)

    def verify_historical_agent(self, cert, event_time):
        return self._registry(cert.get('account_id')).verify_historical_agent(cert, event_time)

    def current_recovery(self): return self.own.current_recovery()

    def profile_owner(self, profile): return self.owners.get(profile, self.own.account_id)

    def role(self, profile, account, at):
        ws = self.workspaces.get(profile)
        return ws.role(account, at) if ws is not None else None

    def known(self, account): return account == self.own.account_id or account in self.foreign

    def accepts(self, account, profile, at, scope=None):
        if account == self.profile_owner(profile): return True
        if not self.known(account): return False
        role = self.role(profile, account, at)
        if role in WRITERS: return True
        # W3: a contributor writes only into the workspace's inbox scopes, as set at the event's time.
        ws = self.workspaces.get(profile)
        return role == 'contributor' and scope is not None and ws is not None \
            and scope in ws.at(at).settings.get('inbox.scopes', [])

    def may_hold(self, profile, account, at):
        if account == self.profile_owner(profile): return True
        return self.known(account) and self.role(profile, account, at) in HOLDERS


def accepts(registry, account, profile, at, scope=None):
    """Event-author acceptance for either registry kind (a plain Registry accepts only its own account)."""
    if isinstance(registry, Federation): return registry.accepts(account, profile, at, scope)
    return account == registry.account_id


def load(storage, own_record, pins=None, *, max_rounds=8):
    """The registry for this device: the plain own-account Registry, or a Federation when workspaces or join
    pins bring in other accounts. Foreign roots are added round by round as trusted member entries pin them."""
    from .registry_store import RegistryStore
    from . import workspace
    own = RegistryStore(storage, own_record).load()
    pins = pins or {}
    profiles = sorted({key.split('/')[2] for key in storage.list(workspace.PREFIX) if key.count('/') >= 3})
    if not pins and not profiles: return own
    owners = {profile: pin['owner'] for profile, pin in pins.items()}
    roots = {pin['owner']: pin['root'] for pin in pins.values() if pin['owner'] != own.account_id}
    foreign = {}
    for _ in range(max_rounds):
        for account, root in roots.items():
            if account not in foreign:
                try: foreign[account] = RegistryStore(storage, account_record(account, root)).load()
                except (ValueError, IdentityError): continue
        federation = Federation(own, foreign, owners, {})
        replayed = {p: workspace.load(storage, p, federation, federation.profile_owner(p)) for p in profiles}
        federation = Federation(own, foreign, owners, replayed)
        found = {}
        for ws in replayed.values():
            for account, root in ws.roots().items():
                if account != own.account_id and account not in roots: found[account] = root
        if not found: return federation
        roots.update(found)
    return federation
