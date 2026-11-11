"""Per-home storage (J1b of the one-click join design): each home writes only its own area, everyone reads all areas.

A workspace remote holds ``homes/<device_id>/<key>`` instead of ``<key>``. ``AreaStorage`` wraps the remote so the
broker's sync logic is unchanged: it sees one flat key space.

- ``put(key)`` writes ``homes/<own device>/<key>``. A credential that can write only that prefix is enough.
- ``list(prefix)`` is the union of that prefix in every area. A key present in any area counts as present, so an
  object another home already published is never uploaded again.
- ``get(key)`` reads the copy in the area the last listing found (own area first), else probes the areas.

Where an object lies does not decide whether it is trusted: every object is signed and checked by readers exactly as in
the flat layout, so a copy in another home's area is the same object. What the layout buys is that a member's
credential cannot delete or overwrite another home's objects. Areas are discovered from the remote's ``homes/``
folders on every listing, so a new member's area is read as soon as it exists.
"""
import re
from . import validate_key, validate_data, ObjectNotFound, StorageError

ROOT = 'homes/'
AREA = re.compile(r'[0-9a-f]{32}')


class AreaStorage:
    def __init__(self, base, device_id):
        if type(device_id) is not str or not AREA.fullmatch(device_id): raise ValueError('invalid device')
        self.base, self.own = base, device_id
        self.backend = getattr(base, 'backend', 'filesystem')
        self.found = {}

    def __getattr__(self, name):
        # The base's I/O counters, for sync status; absent when the base has none (getattr defaults apply).
        if name == 'io': return self.base.io
        raise AttributeError(name)

    def areas(self):
        names = {self.own}
        folders = getattr(self.base, 'folders', None)
        if folders is not None:   # one cheap delimiter listing instead of every key
            names.update(n for n in folders(ROOT) if AREA.fullmatch(n))
            return sorted(names, key=lambda a: (a != self.own, a))
        for key in self.base.list(ROOT):
            parts = key.split('/', 2)
            if len(parts) == 3 and AREA.fullmatch(parts[1]): names.add(parts[1])
        return sorted(names, key=lambda a: (a != self.own, a))

    def put(self, key, data):
        validate_key(key); validate_data(data)
        self.base.put(ROOT + self.own + '/' + key, data)
        self.found[key] = self.own

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

    def list(self, prefix, *, start_after=None):
        validate_key(prefix, prefix=True)
        if start_after is not None: validate_key(start_after)
        keys = {}
        for area in self.areas():
            head = ROOT + area + '/'
            for full in self.base.list(head + prefix):
                key = full[len(head):]
                if start_after is not None and key <= start_after: continue
                keys.setdefault(key, area)
        for key, area in keys.items():
            if self.found.get(key) != self.own: self.found[key] = area
        return iter(sorted(keys))


def wrap(storage, layout, device_id):
    """The remote a home syncs with: ``storage`` itself for the flat layout, an ``AreaStorage`` for per-home."""
    if storage is None or layout in (None, 'flat'): return storage
    if layout != 'per-home': raise StorageError('unknown layout')
    return AreaStorage(storage, device_id)
