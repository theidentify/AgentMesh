"""keys.inventory (M3b): lstat-only key files, the broker's content-free sections, and fixed findings rules."""
import builtins
import os
from pathlib import Path
import pytest
from agentmesh_log import status
from agentmesh_log.control import inventory
from agentmesh_log.control.inventory import BACKUP_MAX_S, DEVICE_STALE_S, KEYS_MAX_S
from agentmesh_log.control.layout import Layout, write_json
from agentmesh_log.control.ops import Control
from agentmesh_log.heartbeats import iso
from .support import FakeBackend

NOW=1_800_000_000.0
DAY=86400
AID='ab'*16
OTHER='cd'*16


def at(seconds_ago): return iso(int((NOW-seconds_ago)*1000))


def device(device_id, *, seen=0, this=False, revoked=False, name='mac'):
    return dict(device_id=device_id,revoked=revoked,this_device=this,name=name,platform='macos',
                last_seen_at=None if seen is None else at(seen),last_sync_at=None,last_sync_outcome=None)


def sections(**change):
    value=dict(
        overview=dict(generated_at=at(60),profiles=[],devices=[device('d1',this=True),device('d2',seen=DAY)],
            agents=[dict(name='reader',read=['work/common'],write=['work/common'],default_write='work/common',
                allow_secret=False,allow_cross_profile=False,allow_import=True)]),
        keys=dict(generated_at=at(60),agents=[dict(name='reader',key_ref=status.key_ref(AID),secret_until=None)],
            recoveries=dict(count=1,latest_issued_at=at(30*DAY))),
        last_backup=dict(at=at(DAY),files=3,bytes=100))
    value.update(change)
    return {k:v for k,v in value.items() if v is not None}


def key(path, mode=0o600, data=b'k'):
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True); path.write_bytes(data); path.chmod(mode); return path


@pytest.fixture
def home(tmp_path):
    layout=Layout(tmp_path).ensure()
    key(layout.agent_keys/(AID+'.key')); key(layout.named_keys/'importer.key'); key(layout.keystore)
    key(layout.named_keys/'README',0o644)  # not a key: never listed or judged
    def write(**change):
        write_json(layout.profile_status,dict(format=status.FORMAT,updated_at=at(0),**sections(**change)))
        layout.profile_status.chmod(0o600)
    write()
    return layout,write


def kinds(result): return {(f['kind'],f['subject']) for f in result['findings']}


def test_healthy_home_has_no_findings(home):
    layout,_=home
    result=inventory.inventory(layout,NOW)
    assert result['findings']==[]
    assert [d['device_id'] for d in result['devices']]==['d1','d2']
    assert result['agents']==[dict(name='reader',key_ref=status.key_ref(AID),secret_until=None,read=['work/common'],write=['work/common'])]
    assert result['recovery']==dict(count=1,latest_issued_at=at(30*DAY)) and result['backup']['files']==3


def test_key_files_listed(home):
    layout,_=home
    rows={r['path']:r for r in inventory.inventory(layout,NOW)['key_files']}
    assert set(rows)=={'profile-log/agents/%s.key'%AID,'agents/importer.key'}
    row=rows['profile-log/agents/%s.key'%AID]
    assert row['kind']=='file' and row['mode']==0o600 and row['owner_ok'] and row['size']==1 and row['agent']=='reader'
    assert rows['agents/importer.key']['agent'] is None
    keystore=inventory.inventory(layout,NOW)['keystore']
    assert keystore['path']=='profile-log/keystore.sealed' and keystore['kind']=='file'


def test_never_reads_a_key(home, monkeypatch):
    layout,_=home
    guarded=(layout.agent_keys,layout.named_keys,layout.keystore)
    def touched(path): return any(Path(path)==g or g in Path(path).parents for g in guarded)
    real_open,real_read_bytes,real_read_text=builtins.open,Path.read_bytes,Path.read_text
    def no(name, real):
        def check(path,*a,**k):
            if touched(path): raise AssertionError('%s read a key: %s'%(name,path))
            return real(path,*a,**k)
        return check
    monkeypatch.setattr(builtins,'open',no('open',real_open))
    monkeypatch.setattr(os,'open',no('os.open',os.open))
    monkeypatch.setattr(Path,'read_bytes',no('read_bytes',real_read_bytes))
    monkeypatch.setattr(Path,'read_text',no('read_text',real_read_text))
    assert len(inventory.inventory(layout,NOW)['key_files'])==2


def test_thresholds_match_the_plan():
    assert (DEVICE_STALE_S,BACKUP_MAX_S,KEYS_MAX_S)==(14*DAY,30*DAY,DAY)


@pytest.mark.skipif(not hasattr(os,'lchmod'),reason='needs lchmod (macOS)')
def test_private_symlink_unsafe(home, tmp_path):
    layout,_=home; path=layout.named_keys/'recall.key'
    path.symlink_to(key(tmp_path/'elsewhere.key')); os.lchmod(path,0o600)
    assert kinds(inventory.inventory(layout,NOW))=={('key_file_unsafe','agents/recall.key')}


@pytest.mark.parametrize('problem',['group','world','empty','symlink','directory'],ids=str)
def test_unsafe_key_file(home, tmp_path, problem):
    layout,_=home; path=layout.named_keys/'recall.key'
    if problem=='group': key(path,0o640)
    elif problem=='world': key(path,0o604)
    elif problem=='empty': key(path,data=b'')
    elif problem=='symlink': path.symlink_to(key(tmp_path/'elsewhere.key'))
    else: path.mkdir()
    assert kinds(inventory.inventory(layout,NOW))=={('key_file_unsafe','agents/recall.key')}


def test_wrong_owner(home, monkeypatch):
    layout,_=home; uid=os.getuid(); monkeypatch.setattr(inventory.os,'getuid',lambda: uid+1)
    found=kinds(inventory.inventory(layout,NOW))
    assert ('keystore_unsafe','profile-log/keystore.sealed') in found and ('key_file_unsafe','agents/importer.key') in found


@pytest.mark.parametrize('problem',['group','missing'],ids=str)
def test_unsafe_keystore(home, problem):
    layout,_=home
    if problem=='group': layout.keystore.chmod(0o640)
    else: layout.keystore.unlink()
    assert kinds(inventory.inventory(layout,NOW))=={('keystore_unsafe','profile-log/keystore.sealed')}


def test_symlinked_key_dir(home, tmp_path):
    layout,_=home
    target=tmp_path/'real'; layout.named_keys.rename(target); layout.named_keys.symlink_to(target)
    result=inventory.inventory(layout,NOW)
    assert kinds(result)=={('key_file_unsafe','agents')} and all(not r['path'].startswith('agents/') for r in result['key_files'])


def test_orphan(home):
    layout,_=home; key(layout.agent_keys/(OTHER+'.key'))
    result=inventory.inventory(layout,NOW)
    assert kinds(result)=={('key_file_orphan','profile-log/agents/%s.key'%OTHER)}
    assert [f['severity'] for f in result['findings']]==['info']


def test_no_orphan_without_keys_section(home):
    layout,write=home; key(layout.agent_keys/(OTHER+'.key')); write(keys=None)
    assert ('key_file_orphan','profile-log/agents/%s.key'%OTHER) not in kinds(inventory.inventory(layout,NOW))


@pytest.mark.parametrize('seen,stale',[(DEVICE_STALE_S-60,False),(DEVICE_STALE_S+60,True),(None,True)],ids=['fresh','old','never'])
def test_device_stale(home, seen, stale):
    layout,write=home
    write(overview=dict(sections()['overview'],devices=[device('d1',this=True),device('d2',seen=seen)]))
    assert (('device_stale','d2') in kinds(inventory.inventory(layout,NOW)))==stale


def test_own_or_revoked_never_stale(home):
    layout,write=home
    write(overview=dict(sections()['overview'],devices=[device('d1',this=True,seen=None),device('d2',seen=None,revoked=True)]))
    assert kinds(inventory.inventory(layout,NOW))==set()


def test_legacy_device_rows(home):
    layout,write=home
    write(overview=dict(sections()['overview'],devices=[dict(device_id='d1',revoked=False)]))
    result=inventory.inventory(layout,NOW)
    assert result['devices'][0]['last_seen_at'] is None and ('device_stale','d1') in kinds(result)


@pytest.mark.parametrize('until,active',[(60,True),(-60,False)],ids=['future','past'])
def test_secret_grant(home, until, active):
    layout,write=home
    keys=sections()['keys']; keys['agents'][0]['secret_until']=at(-until)
    write(keys=keys)
    found=inventory.inventory(layout,NOW)
    assert (('secret_grant_active',status.key_ref(AID)) in kinds(found))==active
    assert all(f['severity']=='info' for f in found['findings'])


def test_no_recovery(home):
    layout,write=home
    write(keys=dict(sections()['keys'],recoveries=dict(count=0,latest_issued_at=None)))
    assert kinds(inventory.inventory(layout,NOW))=={('no_recovery','profile')}


@pytest.mark.parametrize('ago,kind',[(None,'backup_never'),(BACKUP_MAX_S+60,'backup_old'),(BACKUP_MAX_S-60,None)],ids=['never','old','fresh'])
def test_backup(home, ago, kind):
    layout,write=home
    write(last_backup=None if ago is None else dict(at=at(ago),files=1,bytes=1))
    assert kinds(inventory.inventory(layout,NOW))==({(kind,'profile')} if kind else set())


@pytest.mark.parametrize('ago,stale',[(KEYS_MAX_S-60,False),(KEYS_MAX_S+60,True)],ids=['fresh','old'])
def test_inventory_stale(home, ago, stale):
    layout,write=home
    write(keys=dict(sections()['keys'],generated_at=at(ago)))
    assert (('inventory_stale','broker') in kinds(inventory.inventory(layout,NOW)))==stale


@pytest.mark.parametrize('problem',['missing','invalid','tampered','unreadable','symlink','not_json'],ids=str)
def test_unreadable_status(home, tmp_path, problem):
    layout,write=home; path=layout.profile_status
    if problem=='missing': path.unlink()
    elif problem=='tampered':
        keys=sections()['keys']; keys['agents'][0]['agent_id']=AID; write(keys=keys)
    elif problem=='unreadable': path.chmod(0)
    elif problem=='invalid': write_json(path,dict(format='other'))
    elif problem=='symlink': path.rename(tmp_path/'real.json'); path.symlink_to(tmp_path/'real.json')
    else: path.write_text('{')
    result=inventory.inventory(layout,NOW)
    assert result['devices'] is None and result['agents'] is None and result['recovery'] is None and result['backup'] is None
    assert kinds(result)=={('inventory_stale','broker'),('backup_never','profile')}
    assert {r['agent'] for r in result['key_files']}=={None}


def test_findings_sorted(home):
    layout,write=home; key(layout.agent_keys/(OTHER+'.key')); write(last_backup=None)
    findings=inventory.inventory(layout,NOW)['findings']
    assert [f['severity'] for f in findings]==['warning','info']
    assert all(f['id']==f['kind']+':'+f['subject'] and set(f)=={'id','severity','kind','subject'} for f in findings)


def test_op(home):
    layout,_=home
    response=Control(layout,FakeBackend()).handle(dict(v=1,id='x',op='keys.inventory',args={}),{})
    assert response['ok'] and response['result']['findings']==[]
    refused=Control(layout,FakeBackend()).handle(dict(v=1,id='x',op='keys.inventory',args=dict(path='/')),{})
    assert refused['error']['code']=='bad_args'
