"""Offline end-to-end dispatch benchmark; run with the prepared interpreter.

Includes detection, encryption, signature verification, durable replica/audit writes,
and incremental indexes for the two setup_home agents. No socket binding or remote.
Reports medians and maxima over full remember+sign and recall calls. Not a pytest test.
"""
import argparse
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from agentmesh_log.broker.service import Broker, Session
from support import setup_home, remember


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--sizes',type=int,nargs='+',default=[1000,5000])
    parser.add_argument('--samples',type=int,default=20)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='am-bench-') as directory:
        home,state,store,agents=setup_home(Path(directory).resolve()); broker=Broker(home)
        session=Session(agents[0].certificate()); count=0
        def write():
            nonlocal count
            result=remember(broker,session,agents[0],body=f'migrate database fixture {count}')
            if 'id' not in result: raise RuntimeError('benchmark write failed')
            count+=1
        try:
            for size in sorted(args.sizes):
                while count<size: write()
                writes=[]; recalls=[]
                for _ in range(args.samples):
                    start=time.perf_counter(); write(); writes.append(time.perf_counter()-start)
                    start=time.perf_counter()
                    result=broker.request(session,dict(op='recall',query='migrate database',limit=12))
                    recalls.append(time.perf_counter()-start)
                    if len(result.get('results',[]))!=12: raise RuntimeError('benchmark recall failed')
                print(json.dumps(dict(events_before_samples=size,events_after_samples=count,samples=args.samples,
                    write_median_ms=round(statistics.median(writes)*1000,3),write_max_ms=round(max(writes)*1000,3),
                    recall_median_ms=round(statistics.median(recalls)*1000,3),recall_max_ms=round(max(recalls)*1000,3))),flush=True)
        finally: broker.close()


if __name__=='__main__': main()
