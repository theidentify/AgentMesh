"""Pure screen model. Receives names/counts/source references, never row bodies."""
from copy import deepcopy
from .. import settings


class Model:
    def __init__(self, user, *, scopes=(), profiles=(), devices=(), projects=(), labels=None,
                 sources=(), ledger=None, fingerprint='', effective=None):
        self.user=deepcopy(user); self.scopes=sorted(scopes); self.profiles=sorted(profiles)
        self.labels=dict(labels or {}); self.devices=deepcopy(devices); self.projects=deepcopy(projects)
        self.sources=deepcopy(sources); self.ledger=deepcopy(ledger); self.fingerprint=fingerprint
        self.effective=deepcopy(effective if effective is not None else user)
        self.pair_scopes=set(); self.request_id=''; self.screen=0

    def edits(self): return deepcopy(self.user)

    def agents(self): return sorted(settings.merge(self.effective,self.user).get('agents',{}))

    def new_agent(self,name):
        settings.validate({'agents':{name:{}}})
        if name in self.agents(): raise ValueError('agent_exists')
        self.user.setdefault('agents',{})[name]={'read':[],'write':[]}

    def toggle(self,agent,scope,mode):
        if agent not in self.agents() or scope not in self.scopes or mode not in ('read','write'): raise ValueError('invalid_settings')
        # Copy only the edited list into the user layer; preserve other local overrides.
        effective=settings.merge(self.effective,self.user).get('agents',{}).get(agent,{})
        values=set(effective.get(mode,[]))
        if scope in values: values.remove(scope)
        else: values.add(scope)
        self.user.setdefault('agents',{}).setdefault(agent,{})[mode]=sorted(values)

    def new_scope(self,path,label='general'):
        if not settings.scope(path) or '/' not in path or path in self.scopes or label not in ('general','internal','secret'): raise ValueError('invalid_settings')
        profile,relative=path.split('/',1)
        if profile not in self.profiles or path.rpartition('/')[0] not in set(self.scopes)|set(self.profiles): raise ValueError('invalid_settings')
        # Declaring profiles/scopes is a full desired set: seed existing topology to avoid deletion proposals.
        profiles=self.user.setdefault('profiles',{})
        for name in self.profiles:
            entry=profiles.setdefault(name,{})
            declared=entry.setdefault('scopes',{})
            for existing in self.scopes:
                if existing.startswith(name+'/'):
                    declared.setdefault(existing[len(name)+1:],{'label':self.labels.get(existing,'general')})
        profiles[profile]['scopes'][relative]={'label':label}
        self.scopes.append(path); self.scopes.sort(); self.labels[path]=label

    def set_target(self,project,target):
        if target!='skip' and target not in self.scopes: raise ValueError('invalid_settings')
        if project is not None and not settings.text(project): raise ValueError('invalid_settings')
        migration=self.user.setdefault('migration',deepcopy(self.effective.get('migration',{})))
        migration.setdefault('projects',{}); migration.setdefault('prefixes',[])
        if project in (None,'@null'): migration['null_project']=target
        elif project=='@global': migration['global']=target
        else: migration.setdefault('projects',{})[project]=target

    def preview(self):
        if self.ledger is None: return None
        result=dict(retired=0,recreated=0,created=0)
        entries={(e['source']['table'],e['source']['id']):e for e in self.ledger}
        migration=settings.merge(self.effective,self.user).get('migration',{})
        routes=migration.get('projects',{})
        seen=set()
        for row in self.sources:
            key=row['table'],row['id']; seen.add(key); old=entries.get(key)
            target=(migration.get('global',row['target']) if row.get('scope')=='global' else
                    migration.get('null_project',row['target']) if row['project'] is None else routes.get(row['project'],row['target']))
            live=old and old['status']!='retired'
            wanted=not row['reason'] and target not in (None,'skip')
            if live and (not wanted or old['scope']!=target): result['retired']+=1
            if wanted and (not live or old['scope']!=target): result['recreated' if old else 'created']+=1
        result['retired']+=sum(e['status']!='retired' for key,e in entries.items() if key not in seen)
        return result

    def approve(self,request,callback):
        if not self.pair_scopes: raise ValueError('pairing_empty')
        if not settings.text(request) or not self.pair_scopes <= set(self.scopes): raise ValueError('invalid_settings')
        return callback(request,sorted(self.pair_scopes))

    def lines(self,screen=None):
        screen=self.screen if screen is None else screen
        effective=settings.merge(self.effective,self.user)
        if screen==0:
            result=['Overview']
            for profile in self.profiles:
                result.append(profile)
                result.extend('  '+s for s in self.scopes if s.startswith(profile+'/'))
            for name,a in sorted(getattr(self,'enforced_agents',effective.get('agents',{})).items()): result.append(name+' R: '+', '.join(a.get('read',[]))+' W: '+', '.join(a.get('write',[])))
            result.extend('Device '+d['device_id']+' revoked='+str(d['revoked']) for d in self.devices)
            result.extend(self.mapping_lines()); return result
        if screen==1:
            result=['Agents x scopes']
            for agent in self.agents():
                for scope in self.scopes:
                    a=effective.get('agents',{}).get(agent,{})
                    result.append(agent+' '+scope+' R['+('x' if scope in a.get('read',[]) else ' ')+'] W['+('x' if scope in a.get('write',[]) else ' ')+']')
            return result
        if screen==2:
            preview=self.preview()
            return ['Mapping',*self.mapping_lines(), 'Preview unavailable (import ledger not available)' if preview is None else 'Preview '+ ' '.join(k+'='+str(v) for k,v in preview.items())]
        return ['Pair approve','Approver fingerprint: '+self.fingerprint,'Request: '+self.request_id,
                *[('['+('x' if s in self.pair_scopes else ' ')+'] '+s) for s in self.scopes],
                'Approval compares the 6-digit code in this terminal.']

    def mapping_lines(self):
        routes=settings.merge(self.effective,self.user).get('migration',{}).get('projects',{})
        return [(p['project'] if p['project'] is not None else '(null)')+' items='+str(p['items'])+' summaries='+str(p['summaries'])+
                ' current='+str(p.get('target') or 'unmapped')+' new='+str(routes.get(p['project'],p.get('target')) or 'unmapped') for p in self.projects]
