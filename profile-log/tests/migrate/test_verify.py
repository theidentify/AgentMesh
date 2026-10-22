"""Live-ledger verification never imports or exposes staging content."""
import json
import sqlite3
from pathlib import Path
import pytest
from test_phase7 import database, mapping, make, migrate, legacy
from agentmesh_log.migrate.verify import verify
from agentmesh_log.broker.keystore import atomic_write
from agentmesh_log.encoding import wire


class Client:
    def __init__(self,device,entries=()): self.device=device; self.entries=entries; self.calls=[]
    def request(self,value):
        self.calls.append(value['op'])
        if value['op']=='whoami': return dict(device_id=self.device,write=['work/a','work/b'])
        assert value==dict(op='import_ledger')
        return dict(entries=self.entries)


def entry(device,ident,*,source_device=None,status='created'):
    source=dict(table='memory_items',id=ident)
    if source_device is not None: source['device']=source_device
    return dict(source=source,device=device,status=status,scope='work/a')


def test_verify_local_priority(tmp_path):
    db=database(tmp_path/'staging.db',(1,2,3,4,5,6)); device='a'*32; foreign='b'*32
    client=Client(device,[entry(foreign,1,source_device=foreign),entry(foreign,2),
        entry(device,3),entry(device,4,status='retired'),
        entry(device,5,status='retired'),entry(device,5,source_device=device),
        entry(device,6),entry(device,6,source_device=device,status='retired')])
    result=verify(db,mapping(),client,binding=dict(device_id=device,staging_node='fixture'))
    assert result==dict(missing=2,retired_but_present=2)
    assert client.calls==['whoami','import_ledger']


def test_verify_skip_and_retirement(tmp_path):
    db=database(tmp_path/'staging.db',(1,2,3)); device='a'*32
    with sqlite3.connect(db) as c:
        c.execute("UPDATE memory_items SET status='resolved' WHERE id=1")
        c.execute("UPDATE memory_items SET scope='project',project='skip-me' WHERE id=2")
        c.execute("INSERT INTO memory_summaries(scope,scope_key,content) VALUES('session','session','private sentinel')")
    c.close()
    route=mapping(); route.value['projects']['skip-me']='skip'
    client=Client(device,[entry(device,1,source_device=device)])
    assert verify(db,route,client,binding=dict(device_id=device,staging_node='fixture'))==dict(missing=1,retired_but_present=0)


@pytest.mark.parametrize('binding',[{},dict(device_id='b'*32,staging_node='fixture'),dict(device_id='a'*32,staging_node='other')])
def test_verify_binding_first(tmp_path,binding):
    db=database(tmp_path/'staging.db'); client=Client('a'*32)
    with pytest.raises(ValueError,match='binding'):
        verify(db,mapping(),client,binding=binding)
    assert client.calls==['whoami']


def test_verify_unmapped(tmp_path):
    db=database(tmp_path/'staging.db'); client=Client('a'*32)
    with sqlite3.connect(db) as c: c.execute("UPDATE memory_items SET scope='project',project='private sentinel'")
    c.close()
    with pytest.raises(ValueError,match='^unmapped staging rows$'):
        verify(db,mapping(),client,binding=dict(device_id='a'*32,staging_node='fixture'))


def test_verify_cli_live(tmp_path,monkeypatch):
    from agentmesh_log import cli
    broker,agent,client=make(tmp_path); db=database(tmp_path/'staging.db')
    try:
        legacy(broker,agent,db); migrate(db,broker,client,'work/b')
        before=set(broker.replica.keys()); client.close=lambda:None
        original=client.request
        calls=[]
        def request(value):
            calls.append(value['op']); assert value['op'] in ('whoami','import_ledger')
            return original(value)
        client.request=request
        monkeypatch.setattr(cli.BrokerClient,'from_file',lambda _:client)
        route=tmp_path/'mapping.json'; atomic_write(route,wire(mapping('work/b').value))
        atomic_write(Path(str(db)+'.profile-log.json'),wire(dict(format='agentmesh-profile-log-bridge-v1',device_id=broker.state.device.device_id,staging_node='fixture')))
        args=cli.parser().parse_args(['migrate','verify','--db',str(db),'--mapping',str(route),'--agent-key',str(tmp_path/'unused.key')])
        assert cli.execute_migration(args)==dict(missing=0,retired_but_present=0)
        assert set(broker.replica.keys())==before and calls==['whoami','import_ledger']
        with sqlite3.connect(db) as c: c.execute("UPDATE memory_items SET content='changed private sentinel'")
        c.close()
        # A hash-free ledger cannot prove that content is unchanged.
        result=cli.execute_migration(args)
        assert result==dict(missing=0,retired_but_present=0) and 'changed' not in result
    finally: broker.close()
