"""Generate disposable data and measure receive; never opens an existing database.

Run with the desired source directory (including an extracted baseline) on PYTHONPATH:
  python support/benchmark_receive_backlog.py --rows 200000 --packets 200
  python support/benchmark_receive_backlog.py --rows 20000 --packets 200 --mode legacy --chain

Chain filenames and mtimes use independent fixed shuffles, so the benchmark
measures dependency retries even when creation-order sorting cannot help.
"""
import argparse
import json
import os
import random
from pathlib import Path
import tempfile
import time
import uuid

import memory_sync as sync
import resolution_apply as app
import resolution_protocol as protocol
import resolution_store as store
import sqlite_memory
from identity_backup import private_mkdir


def run(count, backlog, padding, mode, *, chain=False):
    with tempfile.TemporaryDirectory(prefix='agentmesh-benchmark-') as temporary:
        root=Path(temporary).resolve(); db=root/'synthetic.db'; exchange=root/'exchange'
        sqlite_memory.init_database(db)
        group=str(uuid.UUID(int=1)); filler='x'*padding
        with sync.connect(db) as c,c:
            c.executemany('INSERT INTO summary_state VALUES(?,?,?)',
                          ((f'row-{n}',0,filler) for n in range(count)))
        sync.initialize(db,'windows',group)
        directory=exchange/'changes'/'mac'
        for path in (exchange,directory.parent,directory): private_mkdir(path)
        with sync.connect(db) as c,c:
            if mode in ('indexed','v2'): store.migrate(c)
            identifiers=list(range(1000,1000+backlog))
            if chain: random.Random(47).shuffle(identifiers)
            arrival=list(range(backlog))
            if chain: random.Random(32).shuffle(arrival)
            previous=None
            for n in range(backlog):
                key='row-0' if chain else f'row-{n}'
                parent=c.execute("SELECT revision FROM _sync_shadow WHERE table_name='summary_state' AND key=?",(sync.canonical([key]),)).fetchone()[0]
                origin=parent
                if chain and previous is not None: parent=previous
                change=dict(table='summary_state',key=[key],row=dict(consumer=key,last_event_id=n+1 if chain else 1,updated_at=filler),revision=str(uuid.UUID(int=10000+n)))
                if mode=='v2': change.update(parents=[parent],origin=origin)
                else: change['parent']=parent
                packet=dict(group=group,node='mac',uuid=str(uuid.UUID(int=identifiers[n])),body=[change])
                if mode=='v2':
                    packet.update(kind='change',covers=[],evidence=[])
                    packet.update(format='omp-memory-changes-v2',checksum=protocol.payload_digest(packet))
                else: packet.update(format=sync.FORMAT,checksum=sync.digest(packet['body']))
                path=directory/(packet['uuid']+'.json')
                path.write_text(sync.canonical(packet),encoding='utf-8')
                stamp=1_700_000_000_000_000_000+arrival[n]*1_000_000_000
                os.utime(path,ns=(stamp,stamp))
                previous=change['revision']
            # Local immutable proof inventory, disjoint from incoming changes.
            for n in range(20):
                key=f'own-{n}'; row=dict(consumer=key,last_event_id=1,updated_at='fixture')
                rev=str(uuid.UUID(int=20000+n))
                body=[dict(table='summary_state',key=[key],row=row,revision=rev,parent=None)]
                packet=dict(format=sync.FORMAT,group=group,node='windows',uuid=str(uuid.UUID(int=30000+n)),body=body,checksum=sync.digest(body))
                c.execute('INSERT INTO summary_state VALUES(?,?,?)',(key,1,'fixture'))
                c.execute('INSERT INTO _sync_shadow VALUES(?,?,?,?)',('summary_state',sync.canonical([key]),sync.canonical(row),rev))
                c.execute('INSERT INTO _sync_history VALUES(?,?,?)',(rev,'summary_state',sync.canonical([key])))
                c.execute('INSERT INTO _sync_outbox(uuid,packet) VALUES(?,?)',(packet['uuid'],sync.canonical(packet)))
        size=db.stat().st_size
        metrics=dict(full_rows_calls=0,local_decodes=0,fk_checks=0,fk_cpu_seconds=0.0,
                     apply_attempts=0,indexed_attempts=0,diagnostic_writes=0)
        original_rows=sync.rows; original_decode=app._decode; original_connect=sync.connect
        original_apply=sync._apply; original_indexed=app.receive_indexed
        def apply(c,p):
            metrics['apply_attempts']+=1
            return original_apply(c,p)
        def indexed(*args,**kwargs):
            metrics['indexed_attempts']+=1
            return original_indexed(*args,**kwargs)
        def rows(c):
            metrics['full_rows_calls']+=1
            return original_rows(c)
        def decode(c,p,security):
            if p['node']=='windows': metrics['local_decodes']+=1
            return original_decode(c,p,security)
        class Connection:
            def __init__(self,c): self.c=c
            def __getattr__(self,name): return getattr(self.c,name)
            def __enter__(self): self.c.__enter__(); return self
            def __exit__(self,*args): return self.c.__exit__(*args)
            def executemany(self,sql,values):
                if sql.startswith('INSERT OR REPLACE INTO _sync_diagnostics'):
                    values=list(values); metrics['diagnostic_writes']+=len(values)
                return self.c.executemany(sql,values)
            def execute(self,sql,*args):
                if sql.startswith('INSERT OR REPLACE INTO _sync_diagnostics'): metrics['diagnostic_writes']+=1
                if sql=='PRAGMA foreign_key_check':
                    start=time.process_time(); result=self.c.execute(sql,*args)
                    values=result.fetchall()
                    metrics['fk_cpu_seconds']+=time.process_time()-start; metrics['fk_checks']+=1
                    class Result:
                        def fetchall(self): return values
                    return Result()
                return self.c.execute(sql,*args)
        from contextlib import contextmanager
        @contextmanager
        def connect(*a,**kw):
            with original_connect(*a,**kw) as c: yield Connection(c)
        sync.rows=rows; app._decode=decode; sync.connect=connect
        sync._apply=apply; app.receive_indexed=indexed
        try:
            start=time.process_time(); wall=time.perf_counter()
            result=sync.receive(db,exchange)
            metrics.update(cpu_seconds=time.process_time()-start,wall_seconds=time.perf_counter()-wall)
        finally:
            sync.rows=original_rows; app._decode=original_decode; sync.connect=original_connect
            sync._apply=original_apply; app.receive_indexed=original_indexed
        assert result['applied']==backlog and not any(result[k] for k in ('pending','conflict','invalid')),result
        return dict(rows=count,packets=backlog,outbox=20,mode=mode,shape='chain' if chain else 'independent',database_bytes=size,report=result,**metrics)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rows',type=int,default=200000)
    parser.add_argument('--packets',type=int,default=200)
    parser.add_argument('--padding',type=int,default=450)
    parser.add_argument('--mode',choices=['legacy','indexed','v2'],default='v2')
    parser.add_argument('--chain',action='store_true',help='single-key chain with seeded shuffled filenames and mtimes')
    args=parser.parse_args()
    if args.rows<args.packets or args.packets<1: parser.error('rows >= packets >= 1 required')
    print(json.dumps(run(args.rows,args.packets,args.padding,args.mode,chain=args.chain),sort_keys=True),flush=True)
