"""The broker's content-free `passkeys` section (M3c): ref, label, when and by which device; never a credential."""
import json
import pytest
from phase6_support import device
from test_passkeys import Authenticator, enroll
from agentmesh_log import passkeys, status
from agentmesh_log.broker import service
from agentmesh_log.heartbeats import iso
from agentmesh_log.registry_store import now


@pytest.fixture
def mesh(tmp_path):
    home,store,storage,cr,p,key,pin,broker,bridge=device(tmp_path)
    keys=passkeys.Passkeys(home,store,storage)
    yield keys,store,broker
    broker.close()


def doc(**sections): return dict(format=status.FORMAT,updated_at=status.timestamp(),**sections)


def section(keys, store): return status.passkeys(store.load(),keys.local,now())


def test_shape(mesh):
    keys,store,_=mesh; auth=enroll(keys,store); key=passkeys.credential_hash(auth.id)
    record=keys.credentials()[key]; value=section(keys,store)
    assert set(value)=={'generated_at','items'}
    assert value['items']==[dict(ref=key[:16],label='Test',created_at=iso(record['created_at']),issuer_device=store.load().device.device_id)]
    assert value['items'][0]['ref']==keys.listing()['passkeys'][0]['credential_hash_prefix']
    assert status.valid(doc(passkeys=value))


def test_content_free(mesh):
    keys,store,_=mesh; auth=enroll(keys,store); key=passkeys.credential_hash(auth.id)
    record=keys.credentials()[key]; text=json.dumps(section(keys,store))
    assert not any(s in text for s in (record['credential_id'],record['public_key'],record['rp_id'],key))


def test_sorted_and_removed(mesh):
    keys,store,_=mesh
    hashes=sorted(passkeys.credential_hash(enroll(keys,store,Authenticator()).id) for _ in range(3))
    assert [i['ref'] for i in section(keys,store)['items']]==[h[:16] for h in hashes]
    keys.publish('passkey-removal',hashes[1],credential_id_sha256=hashes[1],removed_at=now())
    assert [i['ref'] for i in section(keys,store)['items']]==[hashes[0][:16],hashes[2][:16]]


def test_tampered_left_out(mesh):
    from agentmesh_log.encoding import wire
    keys,store,_=mesh; key=passkeys.credential_hash(enroll(keys,store).id)
    path=keys.local.root/'registry'/'passkeys'/(key+'.json')
    record=json.loads(path.read_bytes()); record['label']='tampered'; path.write_bytes(wire(record))
    assert section(keys,store)['items']==[]


def item(**change):
    return dict(dict(ref='ab'*8,label='iPhone',created_at=status.timestamp(),issuer_device='cd'*16),**change)


@pytest.mark.parametrize('change',[dict(label='Mac\x7f'),dict(label='é'*100),dict(label='x')],ids=['del','long','short'])
def test_label_rule_is_the_records(change):
    # Anything the record rule accepts must pass, or one label would stop every status write.
    assert passkeys.label_valid(change['label']) and status.valid(doc(passkeys=dict(generated_at='x',items=[item(**change)])))


@pytest.mark.parametrize('bad',[
    item(ref='ab'*7+'a'), item(ref='AB'*8), item(label=''), item(label='x'*101), item(label='a\nb'), item(label='a\x1fb'), item(label=None),
    item(created_at='yesterday'), item(issuer_device='cd'*15), dict(item(),credential_id='x'), {k:v for k,v in item().items() if k!='label'},
],ids=['ref_short','ref_upper','label_empty','label_long','label_newline','label_unit_sep','label_none','created_text','issuer_short','extra','missing'])
def test_refused(bad):
    assert status.valid(doc(passkeys=dict(generated_at='x',items=[item()])))
    assert not status.valid(doc(passkeys=dict(generated_at='x',items=[bad])))


def counted(monkeypatch):
    calls=[]; real=status.passkeys
    def spy(*args): calls.append(1); return real(*args)
    monkeypatch.setattr(status,'passkeys',spy)
    return calls


def test_cache(mesh, monkeypatch):
    keys,store,broker=mesh; calls=counted(monkeypatch); clock=[1_800_000_000.0]
    monkeypatch.setattr(service.time,'time',lambda:clock[0])
    first=broker._passkeys(); assert first['items']==[] and len(calls)==1
    clock[0]+=3599; assert broker._passkeys() is first and len(calls)==1
    enroll(keys,store); broker.refresh()
    assert len(broker._passkeys()['items'])==1 and len(calls)==2
    clock[0]+=3600; broker._passkeys(); assert len(calls)==3


@pytest.mark.parametrize('folder',['passkeys','passkey-removals','devices'],ids=str)
def test_cache_watches(mesh, monkeypatch, folder):
    keys,store,broker=mesh; calls=counted(monkeypatch)
    broker._passkeys(); path=keys.local.root/'registry'/folder; path.mkdir(parents=True,exist_ok=True)
    (path/'new.json').write_bytes(b'{}'); broker._passkeys(); assert len(calls)==2


def test_cache_watches_rewrite(mesh, monkeypatch):
    # A record replaced under the same name (a re-signed passkey) must be recounted too.
    keys,store,broker=mesh; key=passkeys.credential_hash(enroll(keys,store).id); broker.refresh(); calls=counted(monkeypatch)
    broker._passkeys(); path=keys.local.root/'registry'/'passkeys'/(key+'.json')
    path.write_bytes(path.read_bytes()+b' '); broker._passkeys(); assert len(calls)==2


def test_cache_watches_store(mesh, monkeypatch):
    _,_,broker=mesh; calls=counted(monkeypatch)
    broker._passkeys(); broker.fingerprint='changed'; broker._passkeys(); assert len(calls)==2


def test_hook_writes_section(mesh):
    keys,store,broker=mesh; enroll(keys,store); broker.refresh(); views=[]
    broker.on_overview=views.append; broker._publish_overview(0)
    assert set(views[0])=={'overview','keys','passkeys'} and len(views[0]['passkeys']['items'])==1
    assert status.valid(doc(**views[0]))


def test_failure_keeps_other_sections(mesh, monkeypatch):
    _,_,broker=mesh; views=[]
    monkeypatch.setattr(status,'passkeys',lambda *a:1/0)
    broker.on_overview=views.append; broker._publish_overview(0)
    assert set(views[0])=={'overview','keys'} and broker._passkey_cache is None
