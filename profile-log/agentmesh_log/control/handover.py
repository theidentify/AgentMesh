"""Hand the supervisor's background item from its legacy plist to AgentMesh Control, and back. Owner-run, typed presence.

Both run the same label (org.agentmesh.control), and macOS keys its Login Items record on the label. Tested on macOS 27:
- While the legacy plist's job is loaded, SMAppService reports the agent `enabled` and register() changes nothing.
- Register right after the legacy plist leaves ~/Library/LaunchAgents, and launchd binds the new job to the stale legacy
  record: it fails to spawn (EX_CONFIG) in a loop. unregister() then disables that record and register() gets EPERM.
- About a minute after the plist leaves LaunchAgents, macOS drops the stale record, even while its job keeps running.
  The app then sees `notFound`, boots the legacy job out and registers at once (about a second without a supervisor).

So `handover` only parks the plist in <home>/control/supervisor/ and leaves the supervisor running; AgentMesh Control
finishes the move when macOS has forgotten the old item. It never `launchctl disable`s the label: that override would
block the app's job too. `handover --back` puts the plist back once the app has let go of the label.
"""
import os
import shutil
from pathlib import Path
from .launchd import LaunchdBackend, parse_print
from .migrate import audit
from .plist import LABEL

APP=Path.home()/'Applications'/'AgentMesh Control.app'


def legacy_path(agents=None, label=LABEL):
    return Path(agents or Path.home()/'Library'/'LaunchAgents')/(label+'.plist')


def parked_path(layout, label=LABEL): return layout.control/'supervisor'/(label+'.plist')


def app_plist(app=None, label=LABEL): return Path(app or APP)/'Contents'/'Library'/'LaunchAgents'/(label+'.plist')


def loaded(launchd, label=LABEL):
    """Who runs the label now: 'app' (submitted through SMAppService), 'legacy' (a plist path), or None."""
    code,out=launchd.runner(['/bin/launchctl','print',launchd.domain+'/'+label])
    if code!=0: return None
    return 'app' if parse_print(out).get('path','').startswith('(submitted by') else 'legacy'


def handover(layout, *, console, agents=None, app=None, label=LABEL, launchd=None):
    legacy,target=legacy_path(agents,label),parked_path(layout,label)
    if not os.path.lexists(legacy): return {'error':'no_legacy_plist'}
    if legacy.is_symlink() or not legacy.is_file(): return {'error':'unsafe_plist'}
    if not app_plist(app,label).is_file(): return {'error':'app_missing'}
    if os.path.lexists(target): return {'error':'already_parked'}
    if loaded(launchd or LaunchdBackend(),label)=='app': return {'error':'app_registered'}
    summary=('Hand the supervisor (%s) over to AgentMesh Control.\nThe plist moves from %s to %s; the supervisor keeps '
        'running.\nIn about a minute, tick "Run supervisor at login" in AgentMesh Control.\n'
        'Undo: agentmesh-log control handover --back'%(label,legacy,target))
    if not console.confirm('migrate',summary):
        audit(layout,'supervisor.handover',{'label':label},'presence_denied'); return {'error':'presence_required'}
    target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    shutil.move(str(legacy),str(target)); os.chmod(target,0o600)
    audit(layout,'supervisor.handover',{'label':label},'ok')
    return {'ok':True,'parked':str(target),
        'next':'In about a minute, tick "Run supervisor at login" in AgentMesh Control.'}


def hand_back(layout, *, console, agents=None, label=LABEL, launchd=None):
    legacy,target=legacy_path(agents,label),parked_path(layout,label)
    if not os.path.lexists(target): return {'error':'not_parked'}
    if os.path.lexists(legacy): return {'error':'exists'}
    launchd=launchd or LaunchdBackend()
    # The app's job holds the label until the owner unticks it; bootstrapping beside it would fail or clash.
    if loaded(launchd,label)=='app': return {'error':'app_registered'}
    summary='Return the supervisor (%s) to its launchd plist %s.'%(label,legacy)
    if not console.confirm('migrate',summary):
        audit(layout,'supervisor.hand_back',{'label':label},'presence_denied'); return {'error':'presence_required'}
    legacy.parent.mkdir(parents=True,exist_ok=True); shutil.move(str(target),str(legacy))
    if loaded(launchd,label) is None:
        code,_=launchd.runner(['/bin/launchctl','bootstrap',launchd.domain,str(legacy)])
        if code!=0:
            audit(layout,'supervisor.hand_back',{'label':label},'launchctl_failed'); return {'error':'launchctl_failed'}
    audit(layout,'supervisor.hand_back',{'label':label},'ok')
    return {'ok':True,'plist':str(legacy)}
