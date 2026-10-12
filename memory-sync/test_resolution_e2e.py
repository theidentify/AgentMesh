"""Exchange ordering, real signatures, and independent model comparisons."""
import json
import random
import pytest
import memory_sync as sync
import signed_packets as signed
import resolution_protocol as protocol
from test_resolution_apply import (app, peers, branches, edit, retain, resolve, receive,
                                   vector, ref, activate, choices)
from test_resolution_model import oracle_decision
from test_signed_packets import secure_peers
import packet_versions as versions


def put(exchange,p):
    namespace = 'signed-changes' if 'signature' in p else 'changes'
    path = exchange/namespace/p['node']/(p['uuid']+'.json')
    path.parent.mkdir(parents=True,exist_ok=True); path.write_text(sync.canonical(p))
    return path


def test_three_peers_resolution_before_sources(app,tmp_path):
    a,b,c,p,q = branches(tmp_path)
    r = resolve(app,a,[p,q],dict(x=5,y=2,z=4))
    ex = tmp_path/'exchange'; path = put(ex,r)
    assert sync.receive(c,ex)['pending'] == 1
    put(ex,p); put(ex,q)
    result = sync.receive(c,ex)
    assert result['pending'] == result['conflict'] == result['invalid'] == 0
    assert vector(c) == dict(x=5,y=2,z=4)
    with sync.connect(c) as con:
        assert not con.execute('SELECT 1 FROM _sync_diagnostics WHERE path=?',(str(path),)).fetchone()
    assert sync.receive(c,ex)['applied'] == 0
    assert sync.capture(c)['changes'] == 0


def test_late_covered_packet_does_not_restore_diagnostic(app,tmp_path):
    a,b,c,p,q = branches(tmp_path)
    r = resolve(app,a,[p,q],dict(x=5,y=2,z=4))
    retain(b,[p,q]); receive(app,b,r)
    ex = tmp_path/'exchange'; path=put(ex,p)
    with sync.connect(b) as con, con:
        con.execute('INSERT INTO _sync_diagnostics VALUES(?,?,?)',(str(path),'conflict','old'))
        con.execute("INSERT INTO _sync_diagnostics VALUES('unrelated','invalid','preserve')")
    result=sync.receive(b,ex)
    assert result['applied']==result['conflict']==0
    assert result['invalid']==1
    assert sync.status(b)['superseded']==2
    assert vector(b)==dict(x=5,y=2,z=4)
    mutated=json.loads(sync.canonical(p)); mutated['body'][0]['row']['last_event_id']=90
    mutated['checksum']=sync.digest(mutated['body']); put(ex,mutated)
    assert sync.receive(b,ex)['invalid']==2


def test_signed_three_peer_revocation_boundaries(app,secure_peers,tmp_path):
    a,b,ex,sa,sb=secure_peers
    import sqlite_memory
    c=tmp_path/'c.db'; sqlite_memory.init_database(c); sync.initialize(c,'linux',sa.public['group'])
    directory=tmp_path/'keys-c'; pc=signed.init_identity(directory,sa.public['group'],'linux')
    for sec in (sa,sb):
        signed.approve(sec.directory,pc,pc['key_id'],pc['group'],pc['node'],pc['sender'])
        signed.approve(directory,sec.public,sec.public['key_id'],pc['group'],sec.public['node'],sec.public['sender'])
    sc=signed.Security(directory)
    p=edit(a,dict(x=1,y=2),sa); q=edit(b,dict(x=3,z=4),sb)
    r=resolve(app,a,[p,q],dict(x=5,y=2,z=4),sa)
    retain(b,[p,q],sb); receive(app,b,r,sb)
    retain(c,[p,q],sc)
    signed.revoke(sc.directory,sb.public['key_id'])
    with pytest.raises(ValueError,match='revoked'): receive(app,c,r,sc)
    assert vector(c)=={}
    # Exact committed dispositions remain valid after revocation; new evidence does not.
    signed.revoke(sb.directory,sa.public['key_id'])
    put(ex,p); put(ex,r)
    assert sync.receive(b,ex,security=sb)['invalid']==0
    assert vector(b)==dict(x=5,y=2,z=4)
    tampered=json.loads(sync.canonical(r)); tampered['signature']='0'*128
    put(ex,tampered)
    assert sync.receive(b,ex,security=sb)['invalid']==1


@pytest.mark.parametrize('seed',range(40))
def test_randomized_sql_matches_model(app,tmp_path,seed):
    import uuid
    rng=random.Random(seed)
    a,b,c=peers(tmp_path)
    # Independent random legacy histories: varying keys, packet companions,
    # forks from old heads, chains, tombstones, and repeated delivery.
    frontier={k:[] for k in range(rng.randint(2,6))}; packets=[]
    for index in range(rng.randint(3,9)):
        body=[]
        for k in sorted(rng.sample(list(frontier),rng.randint(1,len(frontier)))):
            revision=str(uuid.uuid4())
            parent=rng.choice(frontier[k]) if frontier[k] and rng.random()<.8 else None
            key='k'+str(k)
            row=None if rng.random()<.2 else dict(consumer=key,last_event_id=rng.randrange(50),updated_at='fixture')
            body.append(dict(table='summary_state',key=[key],row=row,revision=revision,parent=parent))
            frontier[k].append(revision)
        packets.append(dict(format=sync.FORMAT,group=pgroup(),node='mac',
                            uuid=str(uuid.uuid4()),body=body,checksum=sync.digest(body)))
    final={ch['key'][0]:None if rng.random()<.25 else rng.randrange(100) for p in packets for ch in p['body']}
    r=resolve(app,b,packets,final)
    # Compute expected heads/rows solely using the gate-1 set-union oracle.
    with sync.connect(b) as con:
        normalized=[versions.decode_packet(con,e,security=None,folder=e['node'],filename=e['uuid']+'.json') for e in [*packets,r]]
        origins={ch.key:ch.origin for n in normalized for ch in n.changes}
        expected=oracle_decision(normalized[-1],normalized,origins,dict(origins))
    ex=tmp_path/'exchange'
    order=rng.sample([*packets,r],len(packets)+1)+rng.choices(packets,k=4)
    for packet in order:
        put(ex,packet); sync.receive(c,ex)
    with sync.connect(c) as con:
        for key,revision,row in expected:
            got=con.execute('SELECT revision,row FROM _sync_shadow WHERE table_name=? AND key=?',(key.table,sync.canonical(list(key.values)))).fetchone()
            assert tuple(got)==(revision,row), f'seed={seed}'
        assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    assert vector(c)=={k:v for k,v in final.items() if v is not None}
    assert sync.capture(c)['changes']==0


def pgroup():
    from test_memory_sync import GROUP
    return GROUP


def test_later_legacy_edit_of_superseded_branch_conflicts(app,tmp_path):
    a,b,c,p,q=branches(tmp_path)
    r=resolve(app,a,[p,q],dict(x=5,y=2,z=4))
    u=edit(b,dict(x=77))  # offline B did not see R; its new edit descends Q
    retain(c,[p,q]); receive(app,c,r)
    ex=tmp_path/'exchange'; put(ex,u)
    report=sync.receive(c,ex)
    assert report['conflict']==1 and report['pending']==0
    assert vector(c)==dict(x=5,y=2,z=4)


def test_resolution_clears_only_bound_diagnostics_atomically(app,tmp_path):
    a,b,c,p,q=branches(tmp_path)
    r=resolve(app,a,[p,q],dict(x=5,y=2,z=4))
    retain(b,[p,q])
    with sync.connect(b) as con, con:
        for path,packet,digest in [('p',p,ref(p).digest),('r',r,ref(r).digest),('wrong',p,'0'*64)]:
            con.execute('INSERT INTO _sync_packet_paths VALUES(?,?,?)',(path,packet['uuid'],digest))
            con.execute('INSERT INTO _sync_diagnostics VALUES(?,?,?)',(path,'pending','resolution_apply_not_enabled'))
        con.execute("INSERT INTO _sync_diagnostics VALUES('unrelated','invalid','keep')")
    receive(app,b,r)
    with sync.connect(b) as con:
        assert {row[0] for row in con.execute('SELECT path FROM _sync_diagnostics')}=={'wrong','unrelated'}
