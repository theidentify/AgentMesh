"""Usage: python measure_broker.py <profile-log root>. Broker-level Class A pages per steady-state cycle at 30k objects."""
import hashlib, math, os, sys, tempfile, time
from pathlib import Path
root=Path(sys.argv[1]).resolve(); sys.path.insert(0,str(root)); os.environ['AGENTMESH_ALLOW_FILE_KEYSTORE']='1'
from unittest.mock import patch
from agentmesh_log.broker.service import Broker
from agentmesh_log.events import seal_event, event_path
from agentmesh_log.encoding import parse
from agentmesh_log.storage.filesystem import FilesystemStorage
from tests.broker.support import setup_home
ALPHA='0123456789ABCDEFGHJKMNPQRSTVWXYZ'
def floor(ms): n=ms<<80; return ''.join(ALPHA[(n>>s)&31] for s in range(125,-1,-5))
class Counting(FilesystemStorage):
    def __init__(s,r): super().__init__(r); s.reset()
    def reset(s): s.lists=[]; s.gets=0
    def list(s,prefix,**kw):
        k=list(super().list(prefix,**kw)); s.lists.append(len(k)); return iter(k)
    def get(s,key): s.gets+=1; return super().get(key)
with tempfile.TemporaryDirectory(dir=os.path.realpath(tempfile.gettempdir())) as d:
    d=Path(d); home,state,store,agents=setup_home(d); remote=Counting(d/'remote')
    scopes=[(t.root.scope_id,sid) for t in state.trees for sid in t.scopes]
    key=state.trees[0].keys[state.grants[agents[0].agent_id].default_write]
    sample=seal_event(key.write_key,agents[0],'history'); base=time.time_ns()//1000000-30000*60000
    b=Broker(home); b.sync_once(remote)  # publish wraps/control objects
    for i in range(30000):
        p,s=scopes[i%len(scopes)]; path=f'events/{p}/{s}/{floor(base+i*60000)}.json'
        for r in (remote.root,home.root/'replica'):
            (r/path).parent.mkdir(parents=True,exist_ok=True); (r/path).write_bytes(sample)
    b.close(); b=Broker(home); b.sync_once(remote)  # start: full listing
    with patch('agentmesh_log.ulid.generate',return_value=floor(time.time_ns()//1000000)):
        data=seal_event(key.write_key,agents[0],'new')
    remote.put(event_path(parse(data)),data); remote.reset(); r=b.sync_once(remote)
    pages=sum(max(1,math.ceil(n/1000)) for n in remote.lists)
    print(f'{root.parent.name}: lists={len(remote.lists)} keys={sum(remote.lists)} gets={remote.gets} pages={pages} month={pages*43200:,} result={r}')
    b.close()
