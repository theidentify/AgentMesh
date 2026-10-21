"""Local immutable encrypted replica and explicit, best-effort synchronization."""
import hashlib
import time
from .. import ulid
from ..errors import ValidationError
from ..storage.filesystem import FilesystemStorage
from ..events import verify_event, event_path, wrap_path
from ..encoding import wire, parse
from ..keys import unwrap_key


def floor_ulid(ms):
    number = max(0,ms) << 80
    return ''.join(ulid.ALPHABET[(number >> shift) & 31] for shift in range(125,-1,-5))


class Replica:
    def __init__(self, home, state, *, clock=time.time, reconcile_interval=86400, skew_margin=600):
        self.storage = FilesystemStorage(home.root/'replica'); self.state = state
        self.remote_keys = set(); self._invalid = {}; self._invalid_token = None
        self.clock = clock; self.reconcile_interval = reconcile_interval; self.skew_margin = skew_margin
        self.last_full = None
        self._hashes = {key: hashlib.sha256(self.storage.get(key)).digest()
            for prefix in ('events/','keys/') for key in self.storage.list(prefix)}
        self._watermark = None
        self.update_state(state)

    def update_state(self, state):
        for tree in state.trees:
            for wrap in tree.wraps.values(): self._put(wrap_path(wrap),wire(wrap))
        self.state = state

    def _put(self, key, data):
        self.storage.put(key,data)
        digest=hashlib.sha256(data).digest()
        if self._hashes.get(key)!=digest:
            self._hashes[key]=digest; self._watermark=None

    def keys(self):
        return set(self._hashes)

    def watermark(self):
        if self._watermark is None:
            digest=hashlib.sha256()
            for key in sorted(self._hashes):
                digest.update(key.encode()); digest.update(self._hashes[key])
            self._watermark=digest.hexdigest()
        return self._watermark

    def outbox(self): return len(self.keys()-self.remote_keys)

    def put_event(self, data):
        event = verify_event(data,self.state.registry()); self._put(event_path(event),data)
        return event

    def _verify_token(self):
        records = self.state.control.get('registry_records', {})
        epochs = sorted([key.scope_id,key.key_id] for t in self.state.trees
            for keys in t.epochs.values() for key in keys)
        parts = [wire(self.state.account.public_record())]
        for path in sorted(records): parts.extend((path.encode('utf-8'),wire(records[path])))
        parts.extend((wire(epochs),wire(sorted(k for k in self._hashes if k.startswith('keys/')))))
        digest = hashlib.sha256()
        for part in parts: digest.update(len(part).to_bytes(8,'big')); digest.update(part)
        return digest.hexdigest()

    def _prefixes(self, keys):
        prefixes = {f'events/{t.root.scope_id}/{sid}/': None for t in self.state.trees for sid in t.scopes}
        for key in keys:
            parts = key.split('/')
            if len(parts)!=4 or parts[0] not in ('events','keys') or not all(parts[1:]): continue
            prefix = f'events/{parts[1]}/{parts[2]}/'; prefixes.setdefault(prefix,None)
            if parts[0]!='events' or not parts[3].endswith('.json'): continue
            try: ms = ulid.timestamp(parts[3][:-5])
            except ValidationError: continue
            if prefixes[prefix] is None or ms>prefixes[prefix]: prefixes[prefix] = ms
        return prefixes

    def prepare(self, *, full=False):
        """Snapshot replica state for plan(); callers that share the replica run this under their lock."""
        token = self._verify_token()
        if token!=self._invalid_token: self._invalid.clear(); self._invalid_token = token
        now = self.clock()
        full = full or self.last_full is None or now-self.last_full>=self.reconcile_interval
        if full: self._invalid.clear()
        return dict(full=full,now=now,local=self.keys(),remote=set(self.remote_keys),skip=frozenset(self._invalid))

    def plan(self, remote, *, full=False, prepared=None):
        """List what changed remotely: everything on start or daily, else keys/ plus each scope after its cursor."""
        p = prepared or self.prepare(full=full); now = p['now']
        if p['full']: remote_keys = set(remote.list('events/')) | set(remote.list('keys/'))
        else:
            remote_keys = set(remote.list('keys/'))
            prefixes = self._prefixes(p['local'] | p['remote'] | remote_keys)
            for prefix,ms in sorted(prefixes.items()):
                if ms is None: remote_keys.update(remote.list(prefix))
                else:
                    cursor_ms = min(ms,int(now*1000))-self.skew_margin*1000
                    cursor = prefix + floor_ulid(max(0,int(cursor_ms)))
                    remote_keys.update(remote.list(prefix,start_after=cursor))
            remote_keys |= p['remote']
        return dict(full=p['full'],now=now,keys=remote_keys,skip=p['skip'])

    def sync_once(self, remote, on_event=None, *, full=False, plan=None):
        result = dict(uploaded=0,downloaded=0,invalid=0,errors=0,skipped=0)
        if plan is None:
            try: plan = self.plan(remote,full=full)
            except Exception: result['errors'] += 1; return result
        # State may change between planning and applying; the next plan retries those objects.
        if self._verify_token()!=self._invalid_token: self._invalid.clear(); self._invalid_token = self._verify_token()
        remote_keys = plan['keys']; self.remote_keys = remote_keys
        if plan['full']: self.last_full = plan['now']
        uploaded = set()
        local = self.keys()
        for key in sorted(local-remote_keys):
            try: remote.put(key,self.storage.get(key)); uploaded.add(key); result['uploaded'] += 1
            except Exception: result['errors'] += 1
        for key in sorted(remote_keys-local):
            if key in plan['skip']: result['skipped'] += 1; continue
            try: data = remote.get(key)
            except Exception: result['errors'] += 1; continue
            try:
                if key.startswith('events/'):
                    event = verify_event(data,self.state.registry())
                    if event_path(event) != key: raise ValueError('invalid object path')
                else:
                    wrap = parse(data)
                    if wrap_path(wrap) != key: raise ValueError('invalid object path')
                    parents = [k for t in self.state.trees for epochs in t.epochs.values() for k in epochs
                               if k.scope_id == wrap['parent_scope'] and k.key_id == wrap['parent_key_id']]
                    if not parents: raise ValueError('unknown wrap parent')
                    unwrap_key(wrap,parents[0])
            except Exception:
                self._invalid[key] = hashlib.sha256(data).hexdigest(); result['invalid'] += 1; continue
            try:
                self._put(key,data); result['downloaded'] += 1
                if key.startswith('events/') and on_event is not None: on_event(event,data)
            except Exception: result['errors'] += 1
        self.remote_keys |= uploaded
        return result
