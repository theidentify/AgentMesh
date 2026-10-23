"""The launchd plist that keeps the supervisor running. The owner installs it; nothing here loads it."""
import plistlib

LABEL='org.agentmesh.control'


def render(layout, python):
    logs=layout.root/'logs'
    return plistlib.dumps(dict(Label=LABEL,ProgramArguments=[python,'-m','agentmesh_log.cli','control','serve'],
        EnvironmentVariables=dict(AGENTMESH_HOME=str(layout.root)),KeepAlive=True,RunAtLoad=True,
        ProcessType='Background',Umask=63,StandardOutPath=str(logs/'control.out.log'),
        StandardErrorPath=str(logs/'control.err.log')))
