"""Local immutable encrypted replica and explicit, best-effort synchronization."""
import hashlib
from ..storage.filesystem import FilesystemStorage
from ..events import verify_event, event_path, wrap_path
from ..encoding import wire, parse
from ..keys import unwrap_key


class Replica:
    def __init__(self, home, state):
        self.storage = FilesystemStorage(home.root/'replica'); self.state = state
        self.remote_keys = set()
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

    def sync_once(self, remote, on_event=None):
        result = dict(uploaded=0,downloaded=0,invalid=0,errors=0)
        try: remote_keys = set(remote.list('events/')) | set(remote.list('keys/'))
        except Exception: result['errors'] += 1; return result
        self.remote_keys = remote_keys
        local = self.keys()
        for key in sorted(local-remote_keys):
            try: remote.put(key,self.storage.get(key)); result['uploaded'] += 1
            except Exception: result['errors'] += 1
        for key in sorted(remote_keys-local):
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
            except Exception: result['invalid'] += 1; continue
            try:
                self._put(key,data); result['downloaded'] += 1
                if key.startswith('events/') and on_event is not None: on_event(event,data)
            except Exception: result['errors'] += 1
        try: self.remote_keys = set(remote.list('events/')) | set(remote.list('keys/'))
        except Exception: result['errors'] += 1
        return result
