"""Operator-approved protocol capability and private confirmation plans."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile

import memory_sync as sync
import resolution_store as store
from signed_packets import no_symlinks, parse, read_local, write_local, private_directory

CAPABILITY = 1


@contextmanager
def snapshot(database, *, writable=False):
    """Copy a stable DB/WAL pair; never open the source with a writable WAL index."""
    db=no_symlinks(database)
    paths=[db,db.with_name(db.name+'-wal')]
    def stamp():
        result=[]
        for p in paths:
            no_symlinks(p)
            try:
                s=p.stat(); result.append((s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns))
            except FileNotFoundError: result.append(None)
        return result
    with tempfile.TemporaryDirectory(prefix='agentmesh-inspect-') as directory:
        target=Path(directory)/'memory.db'
        for attempt in range(3):
            before=stamp()
            if before[0] is None: raise ValueError('database missing')
            for source,dest in zip(paths,[target,Path(str(target)+'-wal')]):
                if source.exists(): shutil.copyfile(source,dest); dest.chmod(0o600)
                else: dest.unlink(missing_ok=True)
            if stamp()==before: break
        else: raise ValueError('database changed during snapshot; retry')
        c=sqlite3.connect(target); c.row_factory=sqlite3.Row
        try:
            c.execute('PRAGMA foreign_keys=ON')
            if not writable: c.execute('PRAGMA query_only=ON')
            yield c
        finally: c.close()


def emission_enabled(c):
    if not store.exists(c): return False
    row=c.execute('SELECT * FROM _sync_protocol WHERE id=1').fetchone()
    if row is None or row['schema_version']>store.SCHEMA_VERSION: raise ValueError('unsupported protocol capability')
    if not row['emission_enabled']: return False
    if row['schema_version']!=store.SCHEMA_VERSION: raise ValueError('activation record requires operator review')
    manifest=sync.load_packet(row['activation_manifest'] or '{}')
    if sync.digest(manifest)!=row['activation_manifest_digest'] or row['minimum_capability']!=CAPABILITY:
        raise ValueError('invalid protocol activation record')
    cfg=sync.config(c)
    if manifest.get('group')!=cfg['group_id'] or manifest.get('node')!=cfg['node']:
        raise ValueError('activation scope mismatch')
    _inventory(manifest.get('inventory'),{cfg['node']})
    return True


def require_capability(runtime, database):
    capability=runtime.get('protocol_capability',0) if isinstance(runtime,dict) else CAPABILITY
    with snapshot(database) as c:
        active=emission_enabled(c)
        if active and (type(capability) is not int or capability<CAPABILITY):
            raise ValueError('database requires resolution protocol capability')


def require_program_capability(database, arguments):
    with snapshot(database) as c:
        if not emission_enabled(c): return
    if len(arguments)>1 and Path(arguments[1]).name=='sync_worker.py':
        raise ValueError('legacy worker lacks required resolution protocol capability')
    try:
        result=subprocess.run([*map(str,arguments),'protocol-capability'],capture_output=True,timeout=10,check=True)
        report=parse(result.stdout)
    except (OSError,ValueError,subprocess.SubprocessError) as exc:
        raise ValueError('program lacks required resolution protocol capability') from exc
    if report != {'format':'agentmesh-protocol-capability-v1','protocol_capability':CAPABILITY}:
        raise ValueError('program lacks required resolution protocol capability')


def binding(runtime):
    from install_adopt import absolute,load,security_paths
    runtime=absolute(runtime); cfg,scope=load(runtime,authoritative=False)
    paths=[runtime,absolute(cfg['database']),absolute(cfg['exchange']),absolute(cfg['exchange'])/'.stfolder']
    security_dir,state=security_paths(cfg,runtime)
    paths.extend([security_dir,state])
    identities=[]
    for p in paths:
        no_symlinks(p)
        try: s=p.stat(); identities.append([str(p),s.st_dev,s.st_ino])
        except FileNotFoundError: identities.append([str(p),None,None])
    trust=[]
    for name in ('identity.json','trust.json'):
        p=security_dir/name
        trust.append(hashlib.sha256(read_local(p)).hexdigest() if p.exists() else None)
    return dict(runtime=str(runtime),runtime_hash=hashlib.sha256(read_local(runtime)).hexdigest(),
                config=cfg,scope=scope,identities=identities,trust=trust)


def plan_path(runtime,digest):
    if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('invalid plan digest')
    return no_symlinks(Path(runtime).parent/'.agentmesh-plans'/ (digest+'.json'))


def save_plan(runtime,value):
    digest=sync.digest(value); path=plan_path(runtime,digest)
    if not path.parent.exists():
        path.parent.mkdir(mode=0o700)
        from signed_packets import windows_private
        windows_private(path.parent,provision=True)
    private_directory(path.parent)
    if path.exists():
        if parse(read_local(path,private=True))!=value: raise ValueError('plan path collision')
    else: write_local(path,value,exclusive=True)
    return digest


def load_plan(runtime,digest,kind):
    value=parse(read_local(plan_path(runtime,digest),private=True))
    if sync.digest(value)!=digest or value.get('format')!=kind: raise ValueError('plan changed')
    if value['binding']!=binding(runtime): raise ValueError('runtime paths or trust changed; preview again')
    return value


def _inventory(value,observed):
    if (not isinstance(value,dict) or set(value)!={'inventory_complete','legacy_workers_drained','peers'}
            or value['inventory_complete'] is not True or value['legacy_workers_drained'] is not True
            or not isinstance(value['peers'],list) or not value['peers']):
        raise ValueError('complete peer inventory and drained legacy workers must be acknowledged')
    nodes=set()
    for peer in value['peers']:
        if (not isinstance(peer,dict) or set(peer)!={'node','capability','software','offline'}
                or peer['node'] not in sync.RANGES or peer['node'] in nodes
                or type(peer['capability']) is not int or peer['capability']<CAPABILITY
                or not isinstance(peer['software'],str) or not peer['software'].strip()
                or type(peer['offline']) is not bool): raise ValueError('peer lacks required protocol capability')
        nodes.add(peer['node'])
    if not observed<=nodes: raise ValueError('peer inventory omits an observed or reserved peer')


def _observed(c,exchange):
    result={sync.config(c)['node']}
    for namespace in ('changes','signed-changes'):
        directory=no_symlinks(Path(exchange)/namespace)
        if directory.exists(): result.update(p.name for p in directory.iterdir() if p.is_dir())
    for row in c.execute('SELECT packet FROM _sync_outbox'):
        result.add(sync.load_packet(row[0])['node'])
    # Durable join reservations include peers that have never published.
    tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if '_sync_join_reservations' in tables:
        result.update(r[0] for r in c.execute('SELECT node FROM _sync_join_reservations'))
    return result


def activation_plan(runtime,peer_inventory):
    bound=binding(runtime); cfg=bound['config']
    with snapshot(cfg['database']) as c:
        _inventory(peer_inventory,_observed(c,cfg['exchange']))
        if emission_enabled(c): raise ValueError('protocol already activated')
        scope=sync.config(c)
    value=dict(format='agentmesh-activation-plan-v1',binding=bound,inventory=peer_inventory,
               group=scope['group_id'],node=scope['node'],minimum_capability=CAPABILITY)
    digest=save_plan(runtime,value)
    return dict(plan_digest=digest,inventory=peer_inventory,minimum_capability=CAPABILITY)


def activate(runtime,plan_digest,*,confirm_word):
    if confirm_word!='ACTIVATE': raise ValueError('ACTIVATE confirmation required')
    value=load_plan(runtime,plan_digest,'agentmesh-activation-plan-v1')
    from worker_lifecycle import recovery
    with recovery(runtime) as (cfg,security):
        load_plan(runtime,plan_digest,'agentmesh-activation-plan-v1')
        with sync.connect(cfg['database']) as c,c:
            c.execute('BEGIN IMMEDIATE')
            if sync.config(c)!={'node':value['node'],'group_id':value['group']}: raise ValueError('activation scope changed')
            _inventory(value['inventory'],_observed(c,cfg['exchange']))
            store.migrate(c)
            manifest={k:value[k] for k in ('inventory','group','node','minimum_capability')}
            c.execute('UPDATE _sync_protocol SET emission_enabled=1,activation_manifest_digest=?,activation_manifest=?,minimum_capability=?',
                      (sync.digest(manifest),sync.canonical(manifest),CAPABILITY))
            # Preserve old evidence before future captures need its origin proofs.
            from conflict_commands import index_exchange
            index_exchange(c,cfg['exchange'],security)
            from resolution_apply import _index_local
            _index_local(c,security)
            if value['binding']!=binding(runtime): raise ValueError('activation inputs changed')
