"""ST-17 counters and persisted epochs, without network."""
import json
import pytest
from agentmesh_log.broker.service import Broker, SyncRemote, sync_status_valid
from agentmesh_log.storage.filesystem import FilesystemStorage
from agentmesh_log import status
from .support import setup_home


def test_counts(tmp_path):
    remote=SyncRemote(FilesystemStorage(tmp_path/'remote'))
    remote.put('events/a',b'123'); assert remote.get('events/a')==b'123'
    assert list(remote.list('events/'))==['events/a']
    assert remote.counters()==dict(bytes_up=3,bytes_down=3,class_a=2,class_b=1)
    with pytest.raises(Exception): remote.get('absent')
    assert remote.counters()['class_b']==2


def test_restart(tmp_path,monkeypatch):
    monkeypatch.setenv('AGENTMESH_ALLOW_FILE_KEYSTORE','1')
    home,*_=setup_home(tmp_path); rows=[]
    def save(row):
        rows.append(row)
        from agentmesh_log.broker.keystore import atomic_write
        atomic_write(home.root/'sync-status.json',json.dumps(row).encode())
    remote=FilesystemStorage(tmp_path/'remote')
    b=Broker(home,on_sync=save)
    try: b.sync_attempt(remote,60)
    finally: b.close()
    first=rows[-1]; assert first['total_class_a']==first['class_a']>0
    b=Broker(home,on_sync=save)
    try: b.sync_attempt(remote,60)
    finally: b.close()
    last=rows[-1]
    assert last['totals_since']==first['totals_since']
    for k in ('bytes_up','bytes_down','class_a','class_b'): assert last['total_'+k]==first['total_'+k]+last[k]
    assert sync_status_valid(last)
    old={k:v for k,v in last.items() if k not in ('bytes_up','bytes_down','class_a','class_b','totals_since') and not k.startswith('total_')}
    assert sync_status_valid(old)
    status.synced(tmp_path,last)
    assert status.valid(json.loads((tmp_path/'profile-log-status.json').read_text()))
