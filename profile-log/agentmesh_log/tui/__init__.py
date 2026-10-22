"""Terminal controllers: settings edits delegate all reconciliation to apply."""
import sys
from .. import settings
from ..apply import apply_settings, open_home, enforced_agent
from ..broker.grants import names
from ..broker.client import BrokerClient
from ..presence import ConsolePresence
from .model import Model


class Controller:
    def __init__(self, model, *, apply_fn=apply_settings, console=None, backend=None, pair_fn=None, output=print, refresh=None):
        self.model=model; self.apply_fn=apply_fn; self.console=console; self.backend=backend
        self.pair_fn=pair_fn; self.output=output; self.refresh=refresh

    def save(self):
        settings.save_user(self.model.edits())
        result=self.apply_fn(console=self.console,backend=self.backend,output=self.output)
        if result.get('ok') and self.refresh is not None: self.model=self.refresh()
        return result

    def pair(self):
        if self.pair_fn is None: raise ValueError('pairing_unavailable')
        return self.model.approve(self.model.request_id,self.pair_fn)


def load_model(backend=None):
    from ..migrate.mapping import plan, project_targets
    from ..migrate.source import rows
    from ..projects import effective_mapping
    from ..pairing import approver_fingerprint
    value=settings.load(); base=settings.root(); home,store=open_home(base,backend); state=store.load()
    ns=names(state); registry=state.registry()
    devices=[dict(device_id=d,revoked=d in getattr(registry,'revocations',{})) for d in sorted(registry._devices)]
    groups=[]; sources=[]; ledger=None
    database=value.get('bridge',{}).get('database')
    if database:
        mapping=effective_mapping(value,state) if 'migration' in value else None
        groups=plan(database,mapping)['projects']
        values=rows(database)
        groups=project_targets(groups,values,mapping)
        for row in values:
            try: target=mapping.target(row) if mapping else None
            except ValueError: target=None
            sources.append({k:row[k] for k in ('table','id','scope','project','reason')} | {'target':target})
        importer=value.get('bridge',{}).get('import_agent')
        if importer in state.control.get('agent_names',{}):
            client=None
            try:
                key=base/'agents'/(importer+'.key'); settings.safe(key)
                client=BrokerClient.from_file(key)
                response=client.request({'op':'import_ledger'})
                if 'entries' in response: ledger=response['entries']
            except (ValueError,OSError): pass
            finally:
                if client is not None: client.close()
    model=Model(settings.read(base/'settings.json'),scopes=ns.values(),profiles=[t.root.name for t in state.trees],devices=devices,projects=groups,
                labels={ns[s.scope_id]:s.default_label.name for t in state.trees for s in t.scopes.values()},sources=sources,ledger=ledger,
                fingerprint=approver_fingerprint(registry,state.device.device_id),effective=value,device_id=state.device.device_id)
    managed={aid:name for name,aid in state.control.get('agent_names',{}).items()}
    model.enforced_agents={managed.get(aid,aid):enforced_agent(state,grant) for aid,grant in state.grants.items()}
    return model


def run_admin(*, menu=False, backend=None, console=None, input_fn=None, output=print, isatty=None, filesystem=None):
    from . import menu_ui, curses_ui
    from ..cli import parser, execute
    input_fn=input if input_fn is None else input_fn
    console=console or ConsolePresence(input_fn=input_fn,output=output)
    def pair(request,scopes):
        args=['pair','approve',request,'--home',str(settings.root()/'profile-log'),
              '--filesystem',str(filesystem or settings.root()/'profile-log'/'replica')]
        for scope in scopes: args+=['--scope',scope]
        return execute(parser().parse_args(args),backend=backend,console=console,input_fn=input_fn)
    controller=Controller(load_model(backend),console=console,backend=backend,pair_fn=pair,output=output,refresh=lambda:load_model(backend))
    if menu or not (isatty or sys.stdout.isatty)() or not curses_ui.available():
        menu_ui.run(controller,input_fn=input_fn,output=output)
    else: curses_ui.start(controller)
    return {'ok':True}
