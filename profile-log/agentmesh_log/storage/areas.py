"""Per-home storage (J1b of the one-click join design): each home writes only its own area, everyone reads all areas.

A workspace remote holds ``homes/<device_id>/<key>`` instead of ``<key>``. ``AreaStorage`` wraps the remote so the
broker's sync logic is unchanged: it sees one flat key space.

- ``put(key)`` writes ``homes/<own device>/<key>``. A credential that can write only that prefix is enough.
- ``list(prefix)`` is the union of that prefix in every area. A key present in any area counts as present, so an
  object another home already published is never uploaded again.
- ``get(key)`` reads the copy in the area the last listing found (own area first), else probes the areas.

Where an object lies does not decide whether it is trusted: every object is signed and checked by readers exactly as in
the flat layout, so a copy in another home's area is the same object. What the layout buys is that a member's
credential cannot delete or overwrite another home's objects.

**Cost.** Listing every area on every listing grows with the number of homes (measured: 408 LIST requests per sync at
50 homes). So each home keeps ``homes/<device>/head.json``: a random generation rewritten after each upload. At the
start of each sync attempt (``wrap``) the wrapper finds the areas once (one delimiter listing), reads each head with a GET
(Class B, cheap) and re-lists only areas whose generation changed; the others are answered from the last listing.
An area without a head is always listed, so older writers stay correct, only slower. The head holds no content: a
random token and a time, nothing a listing would not show.
"""
import json
import re
import secrets
import time
from . import validate_key, validate_data, ObjectNotFound, StorageError

ROOT = 'homes/'
HEAD = 'head.json'
AREA = re.compile(r'[0-9a-f]{32}')
WINDOW_S = 300.0   # a guard within one sync attempt; wrap() starts every attempt afresh


class AreaStorage:
    def __init__(self, base, device_id, *, window=WINDOW_S, clock=time.monotonic):
        if type(device_id) is not str or not AREA.fullmatch(device_id): raise ValueError('invalid device')
        self.base, self.own, self.window, self.clock = base, device_id, window, clock
        self.backend = getattr(base, 'backend', 'filesystem')
        self.found = {}                 # key -> area holding the copy we read
        self.known = [device_id]        # areas found at the last refresh, own first
        self.seen = {}                  # area -> head generation the cache was built against (None: no head)
        self.cache = {}                 # area -> {prefix: (start_after it was listed with, sorted keys)}
        self.refreshed = None

    def __getattr__(self, name):
        # The base's I/O counters, for sync status; absent when the base has none (getattr defaults apply).
        if name == 'io': return self.base.io
        raise AttributeError(name)

    # ---- areas and heads --------------------------------------------------------------------------------------

    def _discover(self):
        names = {self.own}
        folders = getattr(self.base, 'folders', None)
        if folders is not None:   # one cheap delimiter listing instead of every key
            names.update(n for n in folders(ROOT) if AREA.fullmatch(n))
        else:
            for key in self.base.list(ROOT):
                parts = key.split('/', 2)
                if len(parts) == 3 and AREA.fullmatch(parts[1]): names.add(parts[1])
        return sorted(names, key=lambda a: (a != self.own, a))

    def _head(self, area):
        try: value = json.loads(self.base.get(ROOT + area + '/' + HEAD))
        except ObjectNotFound: return None
        except ValueError: return None
        generation = value.get('generation') if type(value) is dict else None
        return generation if type(generation) is str and re.fullmatch(r'[0-9a-f]{32}', generation) else None

    def _write_head(self):
        generation = secrets.token_hex(16)
        self.base.put(ROOT + self.own + '/' + HEAD, json.dumps({'format': 'agentmesh-area-head-v1', 'generation': generation,
                                                                'at': int(time.time() * 1000)}).encode('utf-8'))
        self.seen[self.own] = generation

    def refresh(self, force=False):
        """Once per sync attempt: find the areas, and drop cached listings of areas whose generation changed (or that
        have no head)."""
        now = self.clock()
        if not force and self.refreshed is not None and now - self.refreshed < self.window: return
        self.known = self._discover()
        for area in self.known:
            # Our own area too: another process of this device (a one-shot `broker sync` beside `broker run`) may have
            # uploaded; our own uploads already updated both the cache and seen[own].
            generation = self._head(area)
            # Another home's area without a head can change unseen, so it is never cached; our own area without a head
            # simply has had no uploads from anyone.
            if (generation is None and area != self.own) or generation != self.seen.get(area): self.cache.pop(area, None)
            self.seen[area] = generation
        self.refreshed = now

    def areas(self):
        self.refresh(); return list(self.known)

    # ---- storage interface ------------------------------------------------------------------------------------

    def put(self, key, data):
        validate_key(key); validate_data(data)
        if key == HEAD: raise StorageError('reserved key')
        self.base.put(ROOT + self.own + '/' + key, data)
        self.found[key] = self.own
        for prefix, (after, keys) in self.cache.get(self.own, {}).items():
            if key.startswith(prefix) and (after is None or key > after) and key not in keys:
                keys.append(key); keys.sort()
        # A new generation after every upload: a reader that listed mid-batch sees a change at its next attempt, and a
        # one-shot sync that exits right after uploading leaves no stale head behind. Uploads are rare next to polling.
        self._write_head()

    def get(self, key):
        validate_key(key)
        tried = []
        for area in ([self.found[key]] if key in self.found else []) + [self.own]:
            if area in tried: continue
            tried.append(area)
            try: return self.base.get(ROOT + area + '/' + key)
            except ObjectNotFound: pass
        for area in self.areas():
            if area in tried: continue
            try:
                data = self.base.get(ROOT + area + '/' + key); self.found[key] = area; return data
            except ObjectNotFound: continue
        raise ObjectNotFound(key)

    def _area_keys(self, area, prefix, start_after):
        # Any cached listing of this prefix or a shorter one answers it (a full sync lists 'events/', incremental
        # syncs list 'events/<profile>/<scope>/'), as long as it was not listed from a later point.
        for cached_prefix, (after, keys) in self.cache.get(area, {}).items():
            if not prefix.startswith(cached_prefix): continue
            if after is None or (start_after is not None and start_after >= after):
                return [k for k in keys if k.startswith(prefix) and (start_after is None or k > start_after)]
        head = ROOT + area + '/'
        # Pass start_after down: incremental sync lists only keys after its floor.
        listed = self.base.list(head + prefix) if start_after is None else self.base.list(head + prefix, start_after=head + start_after)
        keys = sorted(full[len(head):] for full in listed if full[len(head):] != HEAD)
        keys = [k for k in keys if start_after is None or k > start_after]
        if area == self.own or self.seen.get(area) is not None:   # cache only what a head can invalidate
            self.cache.setdefault(area, {})[prefix] = (start_after, keys)
        return list(keys)

    def list(self, prefix, *, start_after=None):
        validate_key(prefix, prefix=True)
        if start_after is not None: validate_key(start_after)
        keys = {}
        for area in self.areas():
            for key in self._area_keys(area, prefix, start_after): keys.setdefault(key, area)
        for key, area in keys.items():
            if self.found.get(key) != self.own: self.found[key] = area
        return iter(sorted(keys))


_SHARED = {}


def _identity(storage):
    root = getattr(storage, 'root', None)
    if root is not None: return ('filesystem', str(root))
    meta = getattr(getattr(storage, 'client', None), 'meta', None)
    return ('s3', getattr(meta, 'endpoint_url', None), getattr(storage, 'bucket', None), getattr(storage, 'prefix', None))


def wrap(storage, layout, device_id):
    """The remote a home syncs with: ``storage`` itself for the flat layout, an ``AreaStorage`` for per-home.

    One ``AreaStorage`` is kept per device and remote for the life of the process, so its heads and listing cache
    survive ``broker run`` rebuilding the S3 client on every attempt; only the base is swapped."""
    if storage is None or layout in (None, 'flat'): return storage
    if layout != 'per-home': raise StorageError('unknown layout')
    key = (device_id, _identity(storage))
    area = _SHARED.get(key)
    if area is None: area = _SHARED[key] = AreaStorage(storage, device_id)
    else: area.base = storage
    area.refreshed = None   # every sync attempt starts by reading the heads again
    return area
