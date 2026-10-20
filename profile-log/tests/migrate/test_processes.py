"""Real IPC concurrency gate; denied socket bind is an explicit failure."""
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import pytest
from test_import import setup_home
from broker.test_isolation import keyfile
from agentmesh_log.broker.service import Broker
from agentmesh_log.broker.transport import UnixServer,PipeServer
from agentmesh_log.broker.client import BrokerClient


def test_p5_4_concurrent_processes():
    root=Path(tempfile.mkdtemp(prefix='am',dir=None if os.name=='nt' else '/tmp')).resolve()
    broker=None
    try:
        home,state,store,agents=setup_home(root); agent=agents[0]
        state.grants[agent.agent_id].allow_import=True; store.save(state)
        broker=Broker(home,store=store)
        try: server=(PipeServer if os.name=='nt' else UnixServer)(home.address(state.device.device_id))
        except PermissionError: pytest.fail('P5-4 blocked: sandbox denies socket binding',pytrace=False)
        thread=threading.Thread(target=broker.serve,kwargs={'server':server},daemon=True); thread.start()
        key=keyfile(home,state,agent); db=root/'synthetic.db'
        # closing(): sqlite3's own context manager commits but keeps the file open (Windows locks it).
        with closing(sqlite3.connect(db)) as c, c:
            c.executescript((Path(__file__).resolve().parents[3]/'memory-sync/schema.sql').read_text(encoding='utf-8'))
            c.executemany("INSERT INTO memory_items(id,kind,scope,content) VALUES(?,'fact','global','synthetic memory')",[(i,) for i in range(1,31)])
        mapping=root/'mapping.json'
        mapping.write_text(json.dumps(dict(format='agentmesh-migration-mapping-v1',projects={},prefixes=[],null_project='work/a',**{'global':'work/a'})),encoding='utf-8')
        env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[2]),'PYTHONDONTWRITEBYTECODE':'1'}
        processes=[]
        for operation in ('run','run','rows'):
            argv=[sys.executable,'-m','agentmesh_log.cli','migrate',operation,'--db',str(db),'--mapping',str(mapping),'--agent-key',str(key)]
            if operation=='rows': argv+=['--ref','memory_items:1']
            processes.append(subprocess.Popen(argv,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8'))
        for process in processes:
            output,_=process.communicate(timeout=60)
            assert process.returncode==0 and not json.loads(output)['errors']
        client=BrokerClient.from_file(key)
        ledger=client.request(dict(op='import_ledger'))['entries']; client.close()
        assert len(ledger)==30 and all(len(e['chain'])==1 for e in ledger)
    finally:
        if broker: broker.close()
        shutil.rmtree(root)
