"""Run from profile-log: python tests/broker/measure_sync_cost.py."""
import hashlib
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from agentmesh_log.broker.keystore import BrokerHome
from agentmesh_log.broker.replica import Replica, floor_ulid
from agentmesh_log.events import seal_event, event_path
from agentmesh_log.encoding import parse
from tests.broker.support import setup_home
from tests.broker.test_replica_sync_cost import CountingStorage


def main():
    started = time.monotonic()
    with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as directory:
        root = Path(directory); home,state,store,agents = setup_home(root)
        remote = CountingStorage(root/'remote'); reader_home = BrokerHome(root/'reader')
        scopes = [(t.root.scope_id,sid) for t in state.trees for sid in t.scopes]
        key = state.trees[0].keys[state.grants[agents[0].agent_id].default_write]
        base = time.time_ns()//1000000+1000
        sample = seal_event(key.write_key,agents[0],'measurement history')
        clock = [(base+29999*60000)/1000]
        reader = Replica(reader_home,state,clock=lambda:clock[0])
        for profile,sid in scopes: (remote.root/f'events/{profile}/{sid}').mkdir(parents=True,exist_ok=True)
        digest = hashlib.sha256(sample).digest()
        for i in range(30000):
            profile,sid = scopes[i%len(scopes)]
            path = f'events/{profile}/{sid}/{floor_ulid(base+i*60000)}.json'
            (remote.root/path).write_bytes(sample)
            reader._hashes[path] = digest
        reader.sync_once(remote)
        clock[0] = (base+30000*60000)/1000
        with patch('agentmesh_log.ulid.generate',return_value=floor_ulid(base+30000*60000)):
            data = seal_event(key.write_key,agents[0],'new event')
        remote.put(event_path(parse(data)),data)
        remote.reset(); result = reader.sync_once(remote)
        assert result==dict(uploaded=0,downloaded=1,invalid=0,errors=0,skipped=0)
        new = (len(remote.lists),sum(n for _,_,n in remote.lists),remote.gets,remote.pages())
        del reader._hashes[event_path(parse(data))]
        remote.reset(); result = reader.sync_once(remote,full=True)
        assert result==dict(uploaded=0,downloaded=1,invalid=0,errors=0,skipped=0)
        full_pages = remote.pages()
        list(remote.list('events/')); list(remote.list('keys/'))
        old = (len(remote.lists),sum(n for _,_,n in remote.lists),remote.gets,remote.pages())
        wraps = next(n for prefix,_,n in remote.lists if prefix=='keys/')
        print('| Algorithm | LIST calls/cycle | Keys listed/cycle | GETs/cycle | Class A pages/cycle | Class A pages/30 days |')
        print('|---|---:|---:|---:|---:|---:|')
        for label,values in [('Old (measured)',old),('Incremental (measured)',new)]:
            calls,keys,gets,pages = values
            print(f'| {label} | {calls:,} | {keys:,} | {gets:,} | {pages:,} | {pages*43200:,} |')
        pages = new[3]+full_pages/1440
        print(f'| Incremental incl. daily reconciliation (amortized) | — | — | — | {pages:,.6f} | {pages*43200:,.0f} |')
        print(f'\nkeys/ objects: {wraps}; scope prefixes: {len(scopes)}; full-cycle Class A pages: {full_pages}.')
        print(f'\n30,000 historical objects across {len(scopes)} scopes; one new event; 60 s historical spacing; 600 s skew margin.')
        print('Historical payloads reused; local hash inventory preseeded; the new event is sealed and verified.')
        print('Monthly figures use 43,200 cycles at 60 s polling; initial sync excluded. Amortized pages add one full cycle per 1,440 polls.')
        print(f'Runtime: {time.monotonic()-started:.2f} s.')


if __name__=='__main__': main()
