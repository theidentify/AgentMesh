"""Disposable offline parity rehearsal. Reports contain metrics and IDs only."""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import random
import secrets
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'profile-log'))
from agentmesh_log.broker.keystore import BrokerHome,FileKeyStore
from agentmesh_log.broker.grants import Grant,resolve,names
from agentmesh_log.broker.client import BrokerClient
from agentmesh_log.broker.service import Broker,Session
from agentmesh_log.identity import AgentIdentity
from agentmesh_log.migrate.mapping import Mapping
from agentmesh_log.migrate.source import rows,body_for,staging_node,check_binding
from agentmesh_log.migrate.run import run as migrate
from agentmesh_log.oskeystore import initialize
from agentmesh_log.presence import CLASSES
import recall_memory


def local_client(broker,agent):
    # Reuse the signing client against the same broker dispatch used by IPC.
    client=BrokerClient.__new__(BrokerClient); client.agent=agent
    session=Session(agent.certificate())
    client.request=lambda request:broker.request(session,request)
    client.close=lambda:broker.imports.release(session)
    return client


def run(database,mapping_file,*,queries=None,sample=200,out=None,inspect=None):
    if type(sample) is not int or sample<0: raise ValueError('invalid sample')
    mapping=Mapping.load(mapping_file); started=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='am-parity-') as directory:
        root=Path(directory).resolve(); root.chmod(0o700)
        copied=Path(database)
        home=BrokerHome(root/'broker')
        profiles=sorted({target.split('/')[0] for target in mapping.targets})
        if not profiles: raise ValueError('no mapped profiles')
        initialize(home,profiles,file_keystore=True)
        store=FileKeyStore(home); state=store.load()
        for target in sorted(mapping.targets,key=lambda n:(n.count('/'),n)):
            parts=target.split('/')
            for size in range(2,len(parts)+1):
                name='/'.join(parts[:size])
                if name not in names(state).values(): state.add(name)
        from agentmesh_log.keyring import initialize_keyring
        from agentmesh_log.storage.filesystem import FilesystemStorage
        initialize_keyring(state,FilesystemStorage(home.root/'replica'))
        writes=[resolve(state,target) for target in sorted(mapping.targets)]
        readers=[resolve(state,p) for p in profiles]
        importer=AgentIdentity(state.device,state.account.account_id)
        reader=AgentIdentity(state.device,state.account.account_id)
        state.grants[importer.agent_id]=Grant(importer.agent_id,importer.public_bytes.hex(),readers,writes,writes[0],readers[0],allow_import=True)
        state.grants[reader.agent_id]=Grant(reader.agent_id,reader.public_bytes.hex(),readers,[],writes[0],readers[0])
        state.control['windows']={kind:int(time.time()*1000)+900000 for kind in CLASSES}
        store.save(state)
        broker=Broker(home,store=store)
        try:
            binding=dict(device_id=state.device.device_id,staging_node=staging_node(copied))
            migration=migrate(copied,mapping,local_client(broker,importer),binding=binding)
            if migration['errors']: raise ValueError('migration failed')
            if inspect: inspect(root,copied,broker)
            values=rows(copied); eligible={p:{'memory_items':[],'memory_summaries':[]} for p in profiles}
            withheld=set(); unmapped=0
            for envelope,payload in broker.payloads():
                if envelope['device']!=state.device.device_id: continue
                body=payload['body']
                if payload['label']=='secret' and isinstance(body,dict) and 'source' in body.get('metadata',{}):
                    source=body['metadata']['source']; withheld.add((source['table'],source['id']))
            for row in values:
                try: target=mapping.target(row)
                except ValueError: unmapped+=1; continue
                if target=='skip': unmapped+=1; continue
                if (row['table'],row['id']) not in withheld:
                    eligible[target.split('/')[0]][row['table']].append(row['id'])
            active=[r for r in values if r['table']=='memory_items' and not r['reason']]
            chosen=random.Random(0).sample(active,min(sample,len(active)))
            query_set=[]
            for row in chosen:
                target=mapping.target(row)
                if target=='skip': continue
                terms=recall_memory.analyze_query(row['content']).terms[:6]
                if terms: query_set.append((' '.join(terms),target.split('/')[0],row.get('project'),row['scope_key'] if row['scope']=='task' else None))
            for project in sorted({r['project'] for r in values if r.get('project')}):
                target=mapping.target(dict(scope='project',project=project))
                if target!='skip': query_set.append((project+' status',target.split('/')[0],project,None))
            if queries:
                for query in Path(queries).read_text(encoding='utf-8').splitlines():
                    if query.strip():
                        for profile in profiles: query_set.append((query,profile,None,None))
            key=secrets.token_bytes(32); failures=[]; overlaps=[]; hits=[]; exact=[]
            client=local_client(broker,reader)
            for query,profile,project,task in query_set:
                old=recall_memory._recall(copied,query,project=project,task=task,limit=12,eligible=eligible[profile])
                def read(plan,pool,config):
                    answer=client.request(dict(op='recall',query=plan.query,project=plan.project,
                        task=plan.task_refs[0] if plan.task_refs else None,profiles=[profile],compat=True,limit=min(pool,100)))
                    if 'error' in answer: raise ValueError('broker unavailable')
                    answer['results']=[r for r in answer['results'] if r.get('source_ref') and (r['source_ref'].get('device',r.get('author_device'))==state.device.device_id)]
                    return answer
                new=recall_memory._recall(copied,query,project=project,task=task,limit=12,bridge_config=binding,broker_reader=read)
                def ids(answer): return [(r['source_type'],r.get('source_ref',{}).get('id',r['id'])) for r in answer['results'] if r['source_type']!='event']
                a,b=ids(old),ids(new); sa,sb=set(a),set(b)
                overlaps.append(len(sa&sb)/len(sa|sb) if sa|sb else 1.)
                hits.append(len(set(a[:5])&set(b[:5]))/len(a[:5]) if a[:5] else 1.)
                exact.append(a==b)
                if a!=b: failures.append(dict(hmac=hmac.new(key,query.encode('utf-8'),hashlib.sha256).hexdigest(),
                    missing_ids=sorted(i for _,i in sa-sb),extra_ids=sorted(i for _,i in sb-sa)))
            result=dict(overlap_at_12=sum(overlaps)/len(overlaps) if overlaps else 1.,
                top5_hit=sum(hits)/len(hits) if hits else 1.,exact_order=sum(exact)/len(exact) if exact else 1.,
                failures=failures,counts=dict(queries=len(query_set),withheld=len(withheld),unmapped=unmapped,
                    items=sum(r['table']=='memory_items' for r in values),summaries=sum(r['table']=='memory_summaries' for r in values)),
                timings=dict(seconds=round(time.monotonic()-started,3)),**compare(copied,mapping,broker,binding))
        finally: broker.close()
    if out is not None:
        fd=os.open(out,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as stream: json.dump(result,stream,ensure_ascii=False)
    return result



def compare(database,mapping,broker,binding):
    from agentmesh_log.migrate.run import local_entry
    check_binding(database,binding,broker.state.device.device_id)
    device=broker.state.device.device_id; missing=changed=0
    for row in rows(database):
        target=mapping.target(row)
        if row['reason'] or target=='skip': continue
        head=broker.imports.head((device,row['table'],row['id']))
        if head is None:
            old=broker.imports.head((None,row['table'],row['id']))
            if old is not None and old[0]['device']==device: head=old
        if head is None: missing+=1; continue
        env,payload=head; meta=payload['body']['metadata']
        expected=body_for(row,target)[0]['metadata']['row_hash']
        if meta['retired'] or meta['row_hash']!=expected or names(broker.state)[env['scope']]!=target: changed+=1
    return dict(missing=missing,changed=changed)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',required=True); parser.add_argument('--mapping',required=True)
    parser.add_argument('--queries'); parser.add_argument('--sample',type=int,default=200); parser.add_argument('--out',required=True)
    args=parser.parse_args(argv)
    try:
        result=run(args.db,args.mapping,queries=args.queries,sample=args.sample,out=args.out)
        print(json.dumps({k:result[k] for k in ('overlap_at_12','top5_hit','exact_order','counts','timings')}))
        return 0
    except Exception as exc:
        print(json.dumps(dict(error=type(exc).__name__)),file=sys.stderr); return 1


if __name__=='__main__': raise SystemExit(main())
