"""Status gates use only generated keys and synthetic rows in disposable homes."""
import json
from pathlib import Path
import sqlite3
import sys
import pytest
from test_apply import env, desired, console, run
from agentmesh_log import status, settings, cli
from agentmesh_log.broker.keystore import atomic_write
from agentmesh_log.encoding import wire, parse
from agentmesh_log.migrate.mapping import Mapping, plan, project_targets
from agentmesh_log.migrate.source import rows


@pytest.fixture
def configured(env):
    root,home,store=env; db=root/'fixture.db'
    with sqlite3.connect(db) as c:
        c.executescript((Path(__file__).resolve().parents[3]/'memory-sync/schema.sql').read_text())
        c.execute("INSERT INTO memory_items(kind,scope,project,content) VALUES('fact','project','AgentMesh','ROW_BODY_SENTINEL')")
        c.execute("INSERT INTO memory_summaries(scope,scope_key,content) VALUES('project','AgentMesh','SUMMARY_BODY_SENTINEL')")
    c.close()
    value=desired(); value['migration']={'global':'work/common','null_project':'work/common','projects':{'AgentMesh':'work/common'},'prefixes':[]}
    value['bridge']={'enabled':True,'profile':'work','recall_agent':'reader','import_agent':'reader'}
    settings.save_user(value)
    atomic_write(root/'settings.local.json',wire({'bridge':{'database':str(db),'authority':True,'cli_argv':[sys.executable,'-m','agentmesh_log.cli']}}))
    return root,home,store,db


def read(root): return json.loads((root/'profile-log-status.json').read_text())


def arguments(root,db):
    return cli.parser().parse_args(['migrate','catch-up','--db',str(db),'--mapping',str(root/'migration-mapping.json'),'--agent-key',str(root/'agents/reader.key')])


class Client:
    def __init__(self): self.intents=[]; self.closed=False
    def request(self,value):
        return {'entries':[]} if value['op']=='import_ledger' else {'write':['work/common']}
    def import_row(self,**value): self.intents.append(value); return {'outcome':'created','label':'general'}
    def close(self): self.closed=True


def test_g8_apply_and_real_catch_up_content(configured,monkeypatch):
    root,home,store,db=configured
    assert run(console=console())['ok']
    before=read(root); assert before['last_apply']['outcome']=='approved'
    assert before['overview']['agents'][0]['name']=='reader'
    assert before['overview']['devices'] and before['overview']['profiles']
    assert before['migration']['projects']==[dict(project='AgentMesh',items=1,summaries=1,target='work/common')]
    client=Client(); monkeypatch.setattr(cli.BrokerClient,'from_file',lambda _:client)
    result=cli.execute_migration(arguments(root,db)); assert result['outcomes']=={'created':2} and client.closed
    value=read(root); assert value['overview']==before['overview'] and value['last_apply']==before['last_apply']
    assert value['last_catch_up']['outcome']=='ok' and value['format']==status.FORMAT
    assert status.valid(value)
    assert (root/'profile-log-status.json').stat().st_mode & 0o777==0o600
    # These are generated fixture secrets only, never application keys or memory.
    key_path=root/'agents/reader.key'; raw_key=key_path.read_bytes(); key=parse(raw_key)
    grant=next(iter(store.load().grants.values()))
    forbidden=[raw_key,str(key_path).encode(),key['agent_private_key'].encode(),grant.agent_id.encode(),grant.agent_public_key.encode(),str(db).encode(),b'ROW_BODY_SENTINEL',b'SUMMARY_BODY_SENTINEL']
    forbidden += [intent['row_hash'].encode() for intent in client.intents]
    raw=(root/'profile-log-status.json').read_bytes()
    assert all(marker not in raw for marker in forbidden)
    assert run()['ok']; assert read(root)['last_catch_up']==value['last_catch_up']


def test_g9_dry_run_unchanged_and_rollback_snapshot(configured,monkeypatch):
    root,home,store,db=configured; path=root/'profile-log-status.json'
    assert run(dry_run=True)['ok'] and not path.exists()
    assert run(console=console())['ok']; before=path.read_bytes()
    assert run(dry_run=True)['ok'] and path.read_bytes()==before
    value=desired(); value['agents']['second']={'read':['work']}; settings.save_user(value)
    from agentmesh_log.broker.keystore import FileKeyStore
    def fail(*_): raise OSError('PRIVATE_MESSAGE')
    monkeypatch.setattr(FileKeyStore,'save',fail)
    assert run(console=console())['error']=='apply_failed'
    value=read(root); assert value['last_apply']['outcome']=='apply_failed'
    assert [a['name'] for a in value['overview']['agents']]==['reader']
    assert 'PRIVATE_MESSAGE' not in json.dumps(value)


@pytest.mark.parametrize('unsafe',['symlink','unwritable'])
def test_g9_writer_failure_return_and_exit_isolation(configured,monkeypatch,unsafe):
    root,home,store,db=configured; assert run(console=console())['ok']
    monkeypatch.setattr(cli.BrokerClient,'from_file',lambda _:Client())
    with monkeypatch.context() as m:
        m.setattr(status,'write',lambda *_,**kw:None)
        expected_apply=run(); expected_catch=cli.execute_migration(arguments(root,db))
    path=root/'profile-log-status.json'; path.unlink()
    if unsafe=='symlink':
        target=root/'protected'; target.write_text('untouched'); path.symlink_to(target)
    else:
        def fail(*_): raise PermissionError('PRIVATE_MESSAGE')
        monkeypatch.setattr(status,'atomic_write',fail)
    monkeypatch.setattr(cli,'emit',lambda _:None)
    assert run()==expected_apply and cli.main(['apply'])==0
    monkeypatch.setattr(cli.BrokerClient,'from_file',lambda _:Client())
    args=arguments(root,db); result=cli.execute_migration(args)
    assert result==expected_catch
    argv=['migrate','catch-up','--db',str(db),'--mapping',args.mapping,'--agent-key',args.agent_key]
    assert cli.main(argv)==0
    def fail_run(*_,**kw): raise ValueError('PRIVATE_MESSAGE')
    monkeypatch.setattr('agentmesh_log.migrate.run.run',fail_run)
    with pytest.raises(ValueError,match='PRIVATE_MESSAGE'): cli.execute_migration(args)
    assert cli.main(argv)==1
    if unsafe=='symlink': assert target.read_text()=='untouched'
    else: assert not path.exists()


def test_g9_catch_up_error_disabled_unmapped_and_section_preservation(configured,monkeypatch):
    root,home,store,db=configured; assert run(console=console())['ok']; overview=read(root)['overview']
    monkeypatch.setattr(cli.BrokerClient,'from_file',lambda _:Client()); args=arguments(root,db)
    def fail(*_,**kw): raise ValueError('PRIVATE_ERROR_MESSAGE')
    with monkeypatch.context() as m:
        m.setattr('agentmesh_log.migrate.run.run',fail)
        with pytest.raises(ValueError,match='PRIVATE_ERROR_MESSAGE'): cli.execute_migration(args)
    value=read(root); assert value['last_catch_up']['outcome']=='error' and value['last_catch_up']['errors']==['ValueError']
    assert 'PRIVATE_ERROR_MESSAGE' not in json.dumps(value) and value['overview']==overview
    with sqlite3.connect(db) as c: c.execute("INSERT INTO memory_items(kind,scope,project,content) VALUES('fact','project','Unmapped','FIXTURE_ONLY')")
    c.close(); assert cli.execute_migration(args)['unmapped']==['Unmapped']
    assert read(root)['last_catch_up']['outcome']=='unmapped'
    atomic_write(Path(str(db)+'.profile-log.json'),wire(dict(format='agentmesh-profile-log-bridge-v1',enabled=False)))
    monkeypatch.setattr(cli.BrokerClient,'from_file',lambda _:pytest.fail('disabled bridge opened client'))
    assert cli.execute_migration(args)['errors']==[]
    value=read(root); assert value['last_catch_up']['outcome']=='disabled' and value['overview']==overview
    assert value['migration']['unmapped']==['Unmapped']


def test_g8_invalid_existing_replaced_and_unsafe_root(configured):
    root,home,store,db=configured; path=root/'profile-log-status.json'
    for raw in (b'{bad',wire({'format':status.FORMAT,'updated_at':'now','key':'FORBIDDEN'})):
        atomic_write(path,raw); status.caught_up(db,None,outcome='disabled')
        assert status.valid(read(root)) and 'FORBIDDEN' not in path.read_text()
    before=path.read_bytes(); root.chmod(0o755)
    try: status.caught_up(db,None,outcome='ok'); assert path.read_bytes()==before
    finally: root.chmod(0o700)


def test_g8_shared_project_targets(configured):
    root,home,store,db=configured; assert run(console=console())['ok']
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO memory_items(kind,scope,project,content) VALUES('fact','global','AgentMesh','fixture')")
        c.execute("INSERT INTO memory_items(kind,scope,project,content) VALUES('fact','project','Skipped','fixture')")
        c.execute("INSERT INTO memory_items(kind,scope,project,content) VALUES('fact','project','Unmapped','fixture')")
    c.close()
    m=Mapping(dict(format='agentmesh-migration-mapping-v1',**{'global':'work','null_project':'work/common','projects':{'AgentMesh':'work/common','Skipped':'skip'},'prefixes':[]}))
    groups=project_targets(plan(db,m)['projects'],rows(db),m)
    assert {g['project']:g['target'] for g in groups}=={'AgentMesh':'mixed','Skipped':'skip','Unmapped':'unmapped'}
    assert status.migration(db,m)['projects']==groups


def test_unknown_reasons_fold_into_other(configured):
    from agentmesh_log.status import caught_up
    root,home,store,db=configured
    caught_up(db,None,dict(outcomes=dict(created=1,brand_new=2),skipped=dict(archived=3,mapping_skip=1),unmapped=[],errors=[]))
    last=read(root)['last_catch_up']
    assert last['outcomes']==dict(created=1,other=2) and last['skipped']==dict(other=3,mapping_skip=1)
