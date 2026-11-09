import json
import os
import stat
from agentmesh_log.control import handover
from agentmesh_log.control.cli import execute
from agentmesh_log.control.layout import Layout
from .support import Presence

LEGACY_PRINT='gui/501/org.agentmesh.control = {\n\tpath = /Users/me/Library/LaunchAgents/org.agentmesh.control.plist\n}\n'
APP_PRINT='gui/501/org.agentmesh.control = {\n\tpath = (submitted by smd.345)\n}\n'


class Launchctl:
    domain='gui/501'

    def __init__(self, who='legacy', fail=False): self.who,self.fail,self.calls=who,fail,[]

    def runner(self, argv):
        self.calls.append(tuple(argv))
        if argv[1]=='print':
            if self.who is None: return (113,'')
            return (0,APP_PRINT if self.who=='app' else LEGACY_PRINT)
        if argv[1]=='bootstrap':
            if self.fail: return (5,'')
            self.who='legacy'
        return (0,'')


def setup(tmp_path, app=True):
    layout=Layout(tmp_path/'home').ensure(); agents=tmp_path/'agents'; agents.mkdir()
    (agents/'org.agentmesh.control.plist').write_bytes(b'<plist/>')
    bundle=tmp_path/'AgentMesh Control.app'
    if app:
        handover.app_plist(bundle).parent.mkdir(parents=True); handover.app_plist(bundle).write_bytes(b'<plist/>')
    return layout,agents,bundle


def outcomes(layout): return [json.loads(line)['outcome'] for line in layout.audit.read_bytes().splitlines()]


def test_handover_parks_only(tmp_path):
    layout,agents,bundle=setup(tmp_path); console=Presence(True); launchctl=Launchctl()
    result=handover.handover(layout,console=console,agents=agents,app=bundle,launchd=launchctl)
    parked=layout.control/'supervisor'/'org.agentmesh.control.plist'
    assert result['ok'] is True and result['parked']==str(parked)
    assert not (agents/'org.agentmesh.control.plist').exists() and parked.read_bytes()==b'<plist/>'
    assert os.name=='nt' or stat.S_IMODE(os.stat(parked).st_mode)==0o600
    # Only a read: no bootout (the app does that) and never a disable (it would block the app's job too).
    assert [c[1] for c in launchctl.calls]==['print']
    assert console.seen[0][0]=='migrate' and 'handover --back' in console.seen[0][1]
    assert outcomes(layout)==['ok']


def test_handover_refusals(tmp_path):
    layout,agents,bundle=setup(tmp_path,app=False)
    run=lambda **kw: handover.handover(layout,console=kw.pop('console',Presence(True)),agents=agents,app=bundle,
        launchd=kw.pop('launchd',Launchctl()))
    assert run()=={'error':'app_missing'}
    handover.app_plist(bundle).parent.mkdir(parents=True); handover.app_plist(bundle).write_bytes(b'x')
    assert run(launchd=Launchctl('app'))=={'error':'app_registered'}
    assert run(console=Presence(False))=={'error':'presence_required'}
    assert (agents/'org.agentmesh.control.plist').exists() and outcomes(layout)==['presence_denied']
    target=handover.parked_path(layout); target.parent.mkdir(parents=True); target.write_bytes(b'old')
    assert run()=={'error':'already_parked'}
    target.unlink(); (agents/'org.agentmesh.control.plist').unlink()
    assert run()=={'error':'no_legacy_plist'}
    os.symlink(tmp_path/'elsewhere',agents/'org.agentmesh.control.plist')
    assert run()=={'error':'unsafe_plist'}


def test_hand_back(tmp_path):
    layout,agents,bundle=setup(tmp_path)
    handover.handover(layout,console=Presence(True),agents=agents,app=bundle,launchd=Launchctl())
    assert handover.hand_back(layout,console=Presence(True),agents=agents,launchd=Launchctl('app'))=={'error':'app_registered'}
    launchctl=Launchctl(None)
    result=handover.hand_back(layout,console=Presence(True),agents=agents,launchd=launchctl)
    legacy=agents/'org.agentmesh.control.plist'
    assert result=={'ok':True,'plist':str(legacy)} and legacy.exists() and not handover.parked_path(layout).exists()
    assert ('/bin/launchctl','bootstrap','gui/501',str(legacy)) in launchctl.calls
    assert handover.hand_back(layout,console=Presence(True),agents=agents,launchd=Launchctl())=={'error':'not_parked'}


def test_back_legacy_still_loaded(tmp_path):
    layout,agents,bundle=setup(tmp_path)
    handover.handover(layout,console=Presence(True),agents=agents,app=bundle,launchd=Launchctl())
    launchctl=Launchctl('legacy')
    assert handover.hand_back(layout,console=Presence(True),agents=agents,launchd=launchctl)['ok'] is True
    assert [c[1] for c in launchctl.calls]==['print','print']


def test_hand_back_bootstrap_failure(tmp_path):
    layout,agents,bundle=setup(tmp_path)
    handover.handover(layout,console=Presence(True),agents=agents,app=bundle,launchd=Launchctl())
    assert handover.hand_back(layout,console=Presence(True),agents=agents,launchd=Launchctl(None,fail=True))=={'error':'launchctl_failed'}
    assert outcomes(layout)[-1]=='launchctl_failed'


def test_cli_needs_agents(tmp_path):
    from agentmesh_log.cli import parser
    layout,agents,bundle=setup(tmp_path)
    run=lambda *argv: execute(parser().parse_args(['control','handover',*argv]),layout=layout,
        console=Presence(True),launchd=Launchctl(None))
    assert run()=={'error':'agents_required'}
    assert run('--agents',str(agents),'--app',str(bundle))['ok'] is True
    assert run('--agents',str(agents),'--back')['ok'] is True


def test_cli_handover_label(tmp_path):
    from agentmesh_log.cli import parser
    layout=Layout(tmp_path/'home').ensure(); agents=tmp_path/'agents'; agents.mkdir()
    (agents/'org.agentmesh.test-control.plist').write_bytes(b'x'); bundle=tmp_path/'T.app'
    handover.app_plist(bundle,'org.agentmesh.test-control').parent.mkdir(parents=True)
    handover.app_plist(bundle,'org.agentmesh.test-control').write_bytes(b'x')
    result=execute(parser().parse_args(['control','handover','--agents',str(agents),'--app',str(bundle),
        '--label','org.agentmesh.test-control']),layout=layout,console=Presence(True),launchd=Launchctl(None))
    assert result['parked']==str(handover.parked_path(layout,'org.agentmesh.test-control'))
