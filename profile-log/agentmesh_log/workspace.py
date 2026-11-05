"""Workspace registry (team workspace W1): signed membership, roles and settings for one profile.

Entries are immutable, content-addressed objects at ``registry/workspace/<profile>/<hash>.json`` (synchronized
with the other ``registry/`` control objects). Each entry is signed by the issuing device and names the entry
it builds on (``previous``), so the workspace is a chain:

- ``genesis`` starts it: the issuer's account becomes the only owner. Only the profile's own account may start it.
- ``member`` sets or removes (``role: null``) one account's role.
- ``settings`` replaces workspace settings (today the task lease limits).

Authorization is checked against the state just before each entry:

- Owners may change any role. A change that touches an owner (the subject is or becomes an owner) needs a
  co-signature from another owner whenever the workspace has two or more owners. The last owner cannot be removed.
- Admins may add, change or remove members and contributors only.
- Owners and admins may change settings. Members and contributors may change nothing.

At a fork (two valid entries naming the same previous) the entry whose issuer has the higher role wins, then
the earlier ``issued_at``, then the lower hash, so a lower role cannot outrun a higher one by racing it.
Times are the issuer's clock. Accounts other than the device registry's own cannot sign until W2.
"""
import hashlib
from dataclasses import dataclass, field
from .encoding import fields, hex_bytes, milliseconds, parse, typed, wire
from .identity import sign, verify_signature
from .errors import IdentityError

FORMAT = 'agentmesh-workspace-entry-v1'
ENTRY_FIELDS = {'format', 'workspace', 'op', 'subject', 'role', 'settings', 'previous', 'issued_at', 'issuer_device',
                'cosigners', 'signature'}
OPS = ('genesis', 'member', 'settings')
ROLES = ('owner', 'admin', 'member', 'contributor')
RANK = {'owner': 3, 'admin': 2, 'member': 1, 'contributor': 0}
MANAGERS = ('owner', 'admin')
DOMAIN = b'AgentMesh/workspace-entry/Ed25519/v1\x00'
COSIGN_DOMAIN = b'AgentMesh/workspace-cosign/Ed25519/v1\x00'
PREFIX = 'registry/workspace/'
MIN_LEASE_MS, DEFAULT_LEASE_MS, MAX_LEASE_MS, HARD_MAX_LEASE_MS = 60_000, 3_600_000, 4 * 3_600_000, 24 * 3_600_000
SETTINGS = {'task_lease.default_ms', 'task_lease.max_ms',
            # W3: agent writes need a person's review; agents listed here write directly (e.g. the tree writer)
            'review.staged', 'review.direct_agents',
            # W3: daily event caps per agent and per member, in each scope of the workspace
            'quota.agent_events_per_day', 'quota.member_events_per_day'}
INT_SETTINGS = {'task_lease.default_ms', 'task_lease.max_ms', 'quota.agent_events_per_day', 'quota.member_events_per_day'}


def entry_hash(entry):
    return hashlib.sha256(b'AgentMesh/workspace-entry-id/v1\x00' + typed(entry)).hexdigest()


def path(entry): return PREFIX + entry['workspace'] + '/' + entry_hash(entry) + '.json'


def _body(entry): return {k: v for k, v in entry.items() if k not in ('signature', 'cosigners')}


def create(device, workspace, op, *, previous=None, subject=None, role=None, settings=None, issued_at=None,
           account_root=None):
    """A new entry signed by ``device`` (a DeviceIdentity); co-signatures are added with ``cosign``.

    ``account_root`` (member entries only) pins another account's root key so its devices can be trusted (W2)."""
    from .registry_store import now
    entry = dict(format=FORMAT, workspace=workspace, op=op, subject=subject, role=role, settings=settings,
                 previous=previous, issued_at=now() if issued_at is None else issued_at,
                 issuer_device=device.device_id, cosigners=[])
    if account_root is not None: entry['account_root'] = account_root
    shape(dict(entry, signature='00' * 64))
    signed = sign(_body(entry), device._private, DOMAIN)
    return dict(signed, cosigners=[])


def cosign(entry, device):
    """Add ``device``'s co-signature over the entry body (another owner approving an owner change)."""
    signature = device._private.sign(COSIGN_DOMAIN + typed(_body(entry))).hex()
    others = [c for c in entry['cosigners'] if c['device_id'] != device.device_id]
    return dict(entry, cosigners=sorted(others + [dict(device_id=device.device_id, signature=signature)],
                                        key=lambda c: c['device_id']))


def settings_valid(settings):
    if type(settings) is not dict or not settings or set(settings) - SETTINGS: return False
    if any(type(settings[k]) is not int for k in INT_SETTINGS & set(settings)): return False
    if settings.get('review.staged', 0) not in (0, 1) or type(settings.get('review.staged', 0)) is not int: return False
    agents = settings.get('review.direct_agents', [])
    if type(agents) is not list or len(agents) > 50 or len(set(map(str, agents))) != len(agents): return False
    try:
        for agent in agents: hex_bytes(agent, 16)
    except (ValueError, TypeError): return False
    for key in ('quota.agent_events_per_day', 'quota.member_events_per_day'):
        if key in settings and not 1 <= settings[key] <= 100_000: return False
    default = settings.get('task_lease.default_ms', DEFAULT_LEASE_MS)
    maximum = settings.get('task_lease.max_ms', MAX_LEASE_MS)
    return MIN_LEASE_MS <= default <= maximum <= HARD_MAX_LEASE_MS


def shape(entry):
    fields(entry, ENTRY_FIELDS | ({'account_root'} if type(entry) is dict and 'account_root' in entry else set()), FORMAT)
    if 'account_root' in entry:
        if entry['op'] != 'member' or entry['role'] is None: raise ValueError('account root only on role grants')
        hex_bytes(entry['account_root'], 32)
    hex_bytes(entry['workspace'], 16); hex_bytes(entry['issuer_device'], 16); hex_bytes(entry['signature'], 64)
    milliseconds(entry['issued_at'])
    if entry['op'] not in OPS: raise ValueError('invalid workspace op')
    if entry['previous'] is not None: hex_bytes(entry['previous'], 32)
    if entry['op'] == 'genesis':
        if entry['previous'] is not None or entry['role'] != 'owner' or entry['settings'] is not None:
            raise ValueError('invalid genesis')
        hex_bytes(entry['subject'], 16)
    elif entry['op'] == 'member':
        hex_bytes(entry['subject'], 16)
        if entry['previous'] is None or entry['settings'] is not None or entry['role'] not in ROLES + (None,):
            raise ValueError('invalid member entry')
    else:
        if entry['previous'] is None or entry['subject'] is not None or entry['role'] is not None \
                or not settings_valid(entry['settings']):
            raise ValueError('invalid settings entry')
    cosigners = entry['cosigners']
    if type(cosigners) is not list or len(cosigners) > 8: raise ValueError('invalid cosigners')
    for c in cosigners:
        if type(c) is not dict or set(c) != {'device_id', 'signature'}: raise ValueError('invalid cosigner')
        hex_bytes(c['device_id'], 16); hex_bytes(c['signature'], 64)
    if len({c['device_id'] for c in cosigners}) != len(cosigners): raise ValueError('duplicate cosigner')
    return entry


def _account(registry, device_id, at):
    """The account of a device whose key was valid at ``at``, or None (unknown, revoked or bad chain)."""
    try: public = registry.device_public(device_id, at=at)
    except (IdentityError, ValueError): return None
    if public is None: return None
    return registry.certificates[device_id]['account_id'], public


def _workstation(registry, device_id):
    """Servers and write-only devices never approve workspace changes (they have no person at them)."""
    return getattr(registry, 'device_classes', {}).get(device_id, 'workstation') == 'workstation'


def verified(registry, entry):
    """(issuer account, {co-signer accounts}) for a well-formed, correctly signed entry, else None."""
    try: shape(entry)
    except (ValueError, KeyError, TypeError): return None
    issuer = _account(registry, entry['issuer_device'], entry['issued_at'])
    if issuer is None or not _workstation(registry, entry['issuer_device']): return None
    try: verify_signature(dict(_body(entry), signature=entry['signature']), issuer[1], DOMAIN)
    except IdentityError: return None
    cosigners = set()
    for c in entry['cosigners']:
        signer = _account(registry, c['device_id'], entry['issued_at'])
        if signer is None or not _workstation(registry, c['device_id']): return None
        try: verify_signature(dict(_body(entry), signature=c['signature']), signer[1], COSIGN_DOMAIN)
        except IdentityError: return None
        cosigners.add(signer[0])
    return issuer[0], cosigners - {issuer[0]}


@dataclass
class State:
    members: dict = field(default_factory=dict)   # account -> role
    settings: dict = field(default_factory=dict)
    roots: dict = field(default_factory=dict)     # account -> pinned root key (hex), kept after removal

    def owners(self): return {a for a, r in self.members.items() if r == 'owner'}

    def lease(self):
        return (self.settings.get('task_lease.default_ms', DEFAULT_LEASE_MS),
                self.settings.get('task_lease.max_ms', MAX_LEASE_MS))


def allowed(state, entry, issuer, cosigners):
    """Whether a verified entry is authorized against the state before it."""
    role = state.members.get(issuer)
    if entry['op'] == 'genesis': return False   # roots are handled by replay
    if role not in MANAGERS: return False
    if entry['op'] == 'settings': return True
    subject, new = entry['subject'], entry['role']
    old = state.members.get(subject)
    pinned = state.roots.get(subject)
    if 'account_root' in entry and pinned is not None and pinned != entry['account_root']: return False
    if old == new and ('account_root' not in entry or pinned is not None): return False
    if role == 'admin':
        return old in (None, 'member', 'contributor') and new in (None, 'member', 'contributor')
    if 'owner' in (old, new):
        owners = state.owners()
        if old == 'owner' and len(owners) == 1: return False   # never leave a workspace without an owner
        if len(owners) >= 2 and not (cosigners & (owners - {issuer})): return False
    return True


def apply(state, entry):
    members, settings, roots = dict(state.members), dict(state.settings), dict(state.roots)
    if 'account_root' in entry: roots.setdefault(entry['subject'], entry['account_root'])
    if entry['op'] == 'genesis': members = {entry['subject']: 'owner'}
    elif entry['op'] == 'member':
        if entry['role'] is None: members.pop(entry['subject'], None)
        else: members[entry['subject']] = entry['role']
    else: settings = dict(entry['settings'])
    return State(members, settings, roots)


@dataclass
class Workspace:
    """The replayed workspace: the winning chain and the state after every entry on it."""
    workspace: str
    history: list = field(default_factory=list)   # [(issued_at, hash, State)] along the winning chain
    superseded: list = field(default_factory=list)
    ignored: list = field(default_factory=list)

    @property
    def exists(self): return bool(self.history)

    @property
    def head(self): return self.history[-1][1] if self.history else None

    @property
    def state(self): return self.history[-1][2] if self.history else State()

    def at(self, when):
        """The state in force at time ``when`` (ms): the last entry on the chain issued at or before it."""
        current = State()
        for issued, _, state in self.history:
            if issued > when: break
            current = state
        return current

    def role(self, account, when): return self.at(when).members.get(account)

    def lease(self, when): return self.at(when).lease()

    def roots(self): return dict(self.state.roots)


def replay(workspace, entries, registry, owner_account):
    """Rebuild one workspace from its entries; ``owner_account`` is the only account allowed to start it."""
    good = {}
    result = Workspace(workspace)
    for entry in entries:
        if type(entry) is not dict or entry.get('workspace') != workspace:
            continue
        check = verified(registry, entry)
        if check is None: result.ignored.append(entry_hash(entry)); continue
        good[entry_hash(entry)] = (entry, check)
    children = {}
    for ident, (entry, _) in good.items(): children.setdefault(entry['previous'], []).append(ident)
    def order(state, ids):
        return sorted(ids, key=lambda i: (-RANK.get(state.members.get(good[i][1][0]), -1), good[i][0]['issued_at'], i))
    roots = [i for i in children.get(None, []) if good[i][0]['op'] == 'genesis'
             and good[i][1][0] == owner_account and good[i][0]['subject'] == owner_account]
    result.ignored += [i for i in children.get(None, []) if i not in roots]
    if not roots: return result
    roots = sorted(roots, key=lambda i: (good[i][0]['issued_at'], i))
    node = roots[0]; result.superseded += roots[1:]
    state = apply(State(), good[node][0]); result.history.append((good[node][0]['issued_at'], node, state))
    while True:
        kids = order(state, children.get(node, []))
        ok = [k for k in kids if good[k][0]['issued_at'] >= good[node][0]['issued_at']
              and allowed(state, good[k][0], *good[k][1])]
        result.ignored += [k for k in kids if k not in ok]
        if not ok: return result
        result.superseded += ok[1:]
        node = ok[0]; state = apply(state, good[node][0])
        result.history.append((good[node][0]['issued_at'], node, state))


def load(storage, workspace, registry, owner_account):
    entries = []
    for key in storage.list(PREFIX + workspace + '/'):
        try:
            entry = parse(storage.get(key))
            if key == path(entry): entries.append(entry)
        except (ValueError, KeyError, TypeError): continue
    return replay(workspace, entries, registry, owner_account)


def publish(storage, entry):
    shape(entry)
    storage.put(path(entry), wire(entry))
    return entry_hash(entry)


def describe(ws):
    return dict(workspace=ws.workspace, exists=ws.exists, head=ws.head,
                members=[dict(account=a, role=r, pinned=a in ws.state.roots) for a, r in sorted(ws.state.members.items())],
                settings=dict(ws.state.settings), lease=dict(zip(('default_ms', 'max_ms'), ws.state.lease())),
                superseded=ws.superseded, ignored=ws.ignored)
