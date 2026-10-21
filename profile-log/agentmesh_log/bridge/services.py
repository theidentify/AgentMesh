"""Render-only bridge services. Never installs or invokes a service manager."""
from pathlib import Path
import plistlib
import subprocess
import xml.etree.ElementTree as ET
from ..broker.keystore import atomic_write


def render(output,home,python,platform,port=8766,*,filesystem=None,s3_bucket=None,s3_prefix='',s3_endpoint=None,s3_credentials=None):
    if platform not in ('macos','windows') or not 1<=port<=65535: raise ValueError('invalid_request')
    if not Path(home).is_absolute() or not Path(python).is_absolute(): raise ValueError('invalid_request')
    output=Path(output)
    if output.exists(): raise ValueError('invalid_request')
    argv=[str(python),'-m','agentmesh_log.cli','bridge','serve','--home',str(home),'--port',str(port)]
    if filesystem and s3_bucket: raise ValueError('invalid_request')
    if filesystem: argv+=['--filesystem',str(filesystem)]
    if s3_bucket:
        from ..storage.r2creds import validate_remote
        validate_remote(s3_credentials,s3_endpoint)
        # A Keychain reference only; the secret itself never enters the rendered file.
        argv+=['--s3-bucket',s3_bucket,'--s3-prefix',s3_prefix,'--s3-endpoint',s3_endpoint,'--s3-credentials',s3_credentials]
    if platform=='macos':
        data=plistlib.dumps({'Label':'org.agentmesh.profile-log.bridge','ProgramArguments':argv,'RunAtLoad':True,'KeepAlive':True})
        filename='org.agentmesh.profile-log.bridge.plist'
    else:
        root=ET.Element('Task',{'version':'1.4','xmlns':'http://schemas.microsoft.com/windows/2004/02/mit/task'})
        triggers=ET.SubElement(root,'Triggers'); ET.SubElement(ET.SubElement(triggers,'LogonTrigger'),'Enabled').text='true'
        principals=ET.SubElement(root,'Principals'); principal=ET.SubElement(principals,'Principal',{'id':'Author'})
        ET.SubElement(principal,'LogonType').text='InteractiveToken'; ET.SubElement(principal,'RunLevel').text='LeastPrivilege'
        settings=ET.SubElement(root,'Settings'); ET.SubElement(settings,'ExecutionTimeLimit').text='PT0S'
        restart=ET.SubElement(settings,'RestartOnFailure'); ET.SubElement(restart,'Interval').text='PT1M'; ET.SubElement(restart,'Count').text='999'
        actions=ET.SubElement(root,'Actions',{'Context':'Author'}); command=ET.SubElement(actions,'Exec')
        ET.SubElement(command,'Command').text=argv[0]; ET.SubElement(command,'Arguments').text=subprocess.list2cmdline(argv[1:])
        data=ET.tostring(root,encoding='utf-8',xml_declaration=True); filename='agentmesh-bridge.xml'
    output.mkdir(mode=0o700,parents=True)
    atomic_write(output/filename,data)
    return {'rendered':str(output/filename)}
