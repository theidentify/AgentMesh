"""Per-home storage (J1b): what a steady-state sync costs as the number of homes grows.

Run from profile-log: python tests/broker/measure_per_home_cost.py

The same setup as measure_sync_cost.py (30,000 historical events, one new event, 60 s polling), with the history
spread over N home areas, each with a head.json, and read through AreaStorage by a reader with its own area.
The new event's area bumps its head, as its writer would. "flat" is the same history in the flat layout. Each row counts
LIST requests (Class A on R2, including the folder listing that finds the areas), keys listed and GETs per cycle.
"""
import hashlib
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agentmesh_log.broker.keystore import BrokerHome
from agentmesh_log.broker.replica import Replica, floor_ulid
from agentmesh_log.events import seal_event, event_path
from agentmesh_log.encoding import parse
from agentmesh_log.storage.areas import AreaStorage, ROOT
from tests.broker.support import setup_home
from tests.broker.test_replica_sync_cost import CountingStorage

HISTORY = 30000
CLASS_A_PER_MILLION = 4.50   # USD, R2 Class A operations (LIST), list price
CLASS_B_PER_MILLION = 0.36   # USD, R2 Class B operations (GET), list price


class Counting(CountingStorage):
    def reset(self): super().reset(); self.folder_calls = 0
    def folders(self, prefix): self.folder_calls += 1; return super().folders(prefix)


def measure(root, homes):
    home, state, store, agents = setup_home(root)
    remote = Counting(root / 'remote')
    scopes = [(t.root.scope_id, sid) for t in state.trees for sid in t.scopes]
    key = state.trees[0].keys[state.grants[agents[0].agent_id].default_write]
    base = time.time_ns() // 1000000 + 1000
    sample = seal_event(key.write_key, agents[0], 'measurement history')
    digest = hashlib.sha256(sample).digest()
    clock = [(base + (HISTORY - 1) * 60000) / 1000]
    reader = Replica(BrokerHome(root / 'reader'), state, clock=lambda: clock[0])
    areas = ['%032x' % (i + 1) for i in range(homes)] if homes else None
    def target(i): return '' if areas is None else ROOT + areas[i % len(areas)] + '/'
    for i in range(HISTORY):
        profile, sid = scopes[i % len(scopes)]
        path = f'events/{profile}/{sid}/{floor_ulid(base + i * 60000)}.json'
        full = remote.root / (target(i) + path); full.parent.mkdir(parents=True, exist_ok=True); full.write_bytes(sample)
        reader._hashes[path] = digest
    if areas is not None:
        for area in areas: (remote.root / (ROOT + area + '/head.json')).write_text('{"generation": "%032x"}' % 1)
    view = remote if areas is None else AreaStorage(remote, 'f' * 32)   # a reader with its own (empty) area
    reader.sync_once(view)
    clock[0] = (base + HISTORY * 60000) / 1000
    with patch('agentmesh_log.ulid.generate', return_value=floor_ulid(base + HISTORY * 60000)):
        data = seal_event(key.write_key, agents[0], 'new event')
    full = remote.root / (target(HISTORY) + event_path(parse(data))); full.parent.mkdir(parents=True, exist_ok=True); full.write_bytes(data)
    if areas is not None:   # the writer of the new event bumped its head; the next attempt reads heads again
        (remote.root / (target(HISTORY) + 'head.json')).write_text('{"generation": "%032x"}' % 2)
        view.refreshed = None
    remote.reset()
    result = reader.sync_once(view)
    assert result['downloaded'] == 1, result
    calls = len(remote.lists) + remote.folder_calls
    pages = remote.pages() + remote.folder_calls
    return calls, sum(n for _, _, n in remote.lists), remote.gets, pages   # GETs include one head per area


def main():
    started = time.monotonic()
    print('| Layout | Homes | LIST calls/cycle | Keys listed/cycle | GETs/cycle | Class A pages/30 days | USD/30 days (A+B) |')
    print('|---|---:|---:|---:|---:|---:|---:|')
    for homes in (None, 1, 5, 20, 50):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as directory:
            calls, keys, gets, pages = measure(Path(directory), homes)
        month = pages * 43200
        usd = month * CLASS_A_PER_MILLION / 1e6 + gets * 43200 * CLASS_B_PER_MILLION / 1e6
        print(f"| {'flat' if homes is None else 'per-home'} | {homes or 1} | {calls:,} | {keys:,} | {gets:,} | {month:,} | {usd:,.2f} |")
    print(f'\n{HISTORY:,} historical events spread evenly over the areas; one new event; 60 s polling (43,200 cycles/30 days).')
    print('Per home and per device: every home in the workspace pays this. Daily reconciliation and uploads excluded.')
    print(f'Runtime: {time.monotonic() - started:.1f} s.')


if __name__ == '__main__': main()
