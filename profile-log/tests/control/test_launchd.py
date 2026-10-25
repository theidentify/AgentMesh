import pytest
from agentmesh_log.control.launchd import LaunchdBackend, parse_print, parse_etime
from agentmesh_log.control.units import get

SAMPLE=('gui/501/org.agentmesh.summary = {\n\tactive count = 1\n\tpath = /x.plist\n\tstate = running\n\n'
        '\tprogram = /p\n\tpid = 4242\n\tlast exit code = 1\n\tevent triggers = {\n\t\tstate = active\n\t}\n}\n')


def test_parse_print_reads_top_level_only():
    fields=parse_print(SAMPLE)
    assert fields['state']=='running' and fields['pid']=='4242' and fields['last exit code']=='1'


def test_parse_etime():
    assert parse_etime('05:03')==303 and parse_etime('02:00:01')==7201
    assert parse_etime('  1-00:00:05\n')==86405 and parse_etime('junk') is None


def test_status_running_with_runtime():
    calls=[]
    def runner(argv):
        calls.append(argv)
        return (0,SAMPLE) if argv[1]=='print' else (0,'  1-00:00:05\n')
    status=LaunchdBackend(runner,uid=501).status(get('summary'))
    assert status==dict(loaded=True,running=True,pid=4242,last_exit=1,runtime_s=86405)
    assert calls==[['/bin/launchctl','print','gui/501/org.agentmesh.summary'],['/bin/ps','-o','etime=','-p','4242']]


def test_status_not_loaded():
    status=LaunchdBackend(lambda argv:(113,''),uid=501).status(get('summary'))
    assert status==dict(loaded=False,running=False,pid=None,last_exit=None,runtime_s=None)


def test_never_exited_is_none():
    text='x = {\n\tstate = not running\n\tlast exit code = (never exited)\n}\n'
    assert LaunchdBackend(lambda argv:(0,text),uid=501).status(get('ingest'))['last_exit'] is None


def test_controls_use_exact_argv():
    calls=[]; backend=LaunchdBackend(lambda argv:(calls.append(argv) or (0,'')),uid=501); unit=get('ingest')
    backend.start(unit); backend.restart(unit); backend.stop(unit)
    target='gui/501/org.agentmesh.ingest'
    assert calls==[['/bin/launchctl','kickstart',target],['/bin/launchctl','kickstart','-k',target],
                   ['/bin/launchctl','kill','SIGTERM',target]]


def test_control_failure_raises():
    with pytest.raises(RuntimeError): LaunchdBackend(lambda argv:(3,''),uid=501).start(get('ingest'))


def test_bootout_and_bootstrap_argv():
    calls=[]; backend=LaunchdBackend(lambda argv:(calls.append(argv) or (0,'')),uid=501); unit=get('ingest')
    backend.bootout(unit); backend.bootstrap(unit,'/x/org.agentmesh.ingest.plist')
    assert calls==[['/bin/launchctl','bootout','gui/501/org.agentmesh.ingest'],
                   ['/bin/launchctl','bootstrap','gui/501','/x/org.agentmesh.ingest.plist']]


def test_disable_and_enable_argv():
    calls=[]; backend=LaunchdBackend(lambda argv:(calls.append(argv) or (0,'')),uid=501); unit=get('retention')
    backend.disable(unit); backend.enable(unit)
    assert calls==[['/bin/launchctl','disable','gui/501/org.agentmesh.retention'],
                   ['/bin/launchctl','enable','gui/501/org.agentmesh.retention']]
    with pytest.raises(RuntimeError): LaunchdBackend(lambda argv:(5,''),uid=501).disable(unit)
