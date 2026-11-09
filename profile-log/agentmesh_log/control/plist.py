"""The launchd plist that keeps the supervisor running. The owner installs it; nothing here loads it.

After `control consolidate --apply` this is the only AgentMesh plist in ~/Library/LaunchAgents: the supervisor runs every
other unit as its child. AssociatedBundleIdentifiers lets System Settings > Login Items attribute the background item to
the AgentMesh Control app when it is installed.
"""
import plistlib

LABEL='org.agentmesh.control'
APP_BUNDLE_ID='org.agentmesh.control-app'


def render(layout, python, label=None):
    logs=layout.root/'logs'
    return plistlib.dumps(dict(Label=label or LABEL,ProgramArguments=[python,'-m','agentmesh_log.cli','control','serve'],
        EnvironmentVariables=dict(AGENTMESH_HOME=str(layout.root)),KeepAlive=True,RunAtLoad=True,
        ProcessType='Background',Umask=63,StandardOutPath=str(logs/'control.out.log'),
        StandardErrorPath=str(logs/'control.err.log'),AssociatedBundleIdentifiers=[APP_BUNDLE_ID]))
