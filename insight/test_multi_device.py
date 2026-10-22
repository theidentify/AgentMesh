"""Multi-device metadata and tunnel gates use only temporary fixtures."""
import json
import os
from pathlib import Path
import plistlib
import subprocess
import pytest
import adapters
from test_profile_log import fixture
from test_sync_view import HARNESS


def test_md5_projection(tmp_path):
    row=dict(device_id='ab'*16,revoked=False,this_device=True,name='My Mac',platform='macos',
        last_seen_at='2026-10-22T00:00:00Z',last_sync_at='2026-10-22T00:00:00+00:00',last_sync_outcome='ok')
    def read(row):
        value=fixture(); value['overview']['devices']=[row]
        (tmp_path/'profile-log-status.json').write_text(json.dumps(value))
        return adapters.profile_log_status(tmp_path)['overview']['devices'][0]
    assert read(row)==dict(row,device_id='ab'*6)
    for key,bad in [('name','x'*65),('name',42),('name','bad\n'),('platform','plan9'),('platform',{}),
            ('last_seen_at','yesterday'),('last_seen_at','2026-10-22'),('last_sync_at',42),
            ('last_sync_outcome','created'),('last_sync_outcome',{}),('this_device',1)]:
        assert read(dict(row,**{key:bad}))[key] is None
    assert set(read(dict(row,secret='PRIVATE')))==set(row)


def test_md6_render():
    script=Path(__file__).with_name('index.html').read_text().split('<script>')[1].split('</script>')[0]
    harness=HARNESS.split('const peer=')[0]+r'''
const attack='<img src=x onerror=1>';
for(const count of [1,3]){
 const rows=Array.from({length:count},(_,i)=>({device_id:'a'.repeat(31)+i,name:i===0?attack:null,platform:i===0?'macos':null,this_device:i===0,revoked:i===2,last_seen_at:i===0?new Date().toISOString():null,last_sync_at:i===0?new Date().toISOString():null,last_sync_outcome:i===0?'ok':null}));
 context.DATA={available:true,overview:{devices:rows}};
 vm.runInContext('renderProfileLog(DATA);ops={};renderSyncSummary()',context);
 for(const id of ['profile-log-devices','sync-profile']){
  const html=element(id).innerHTML;
  assert.equal((html.match(/<tbody><tr>|<\/tr><tr>/g)||[]).length,count);
  assert.ok(html.includes('&lt;img src=x onerror=1&gt;'));
  assert.ok(!html.includes('<img'));
  for(const text of ['macos','this device','last seen','last sync','ok'])assert.ok(html.includes(text),text);
  if(count===3)for(const text of ['never reported','revoked','aaaaaaaaaaaa'])assert.ok(html.includes(text),text);
 }
}
'''
    result=subprocess.run(['node','-e','const SCRIPT='+json.dumps(script)+';'+harness],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_md7_allowlist():
    from remote_access import ALLOWED_PATHS
    assert ALLOWED_PATHS==frozenset(('/', '/metrics.html', '/health', '/api/profile-log', '/api/metrics', '/api/connections', '/api/operations', '/api/storage'))


@pytest.mark.parametrize('mode',['ok','empty','failed','missing-cloudflared','missing-security'])
def test_md9_token(tmp_path,mode):
    script=Path(__file__).with_name('tunnel')/'run-insight-tunnel.sh'
    assert script.is_file()
    token='SYNTHETIC_TUNNEL_TOKEN_123'
    security=tmp_path/'security'; cloud=tmp_path/'cloudflared'; capture=tmp_path/'capture'
    security.write_text('#!/bin/sh\n[ "$*" = "find-generic-password -s org.agentmesh.insight.tunnel -a agentmesh -w" ] || exit 3\n'+
        ('echo '+token+' >&2; exit 1\n' if mode=='failed' else 'exit 0\n' if mode=='empty' else 'echo '+token+'\n'))
    cloud.write_text('#!/bin/sh\nprintf "%s\\n" "$TUNNEL_TOKEN" "$@" > "$CAPTURE"\n')
    security.chmod(0o700); cloud.chmod(0o700)
    if mode=='missing-cloudflared': cloud.unlink()
    if mode=='missing-security': security.unlink()
    result=subprocess.run(['/bin/sh',str(script)],env={'PATH':str(tmp_path),'CAPTURE':str(capture)},capture_output=True,text=True)
    assert token not in result.stdout+result.stderr
    if mode=='ok':
        assert result.returncode==0,result.stderr
        assert capture.read_text().splitlines()==[token,'tunnel','--no-autoupdate','run']
    else:
        assert result.returncode!=0 and result.stderr.strip() and not capture.exists()


def test_md9_plist():
    path=Path(__file__).with_name('tunnel')/'org.agentmesh.insight-tunnel.plist'
    raw=path.read_bytes(); value=plistlib.loads(raw)
    assert value['KeepAlive'] is True and value['RunAtLoad'] is True
    assert value['Label']=='org.agentmesh.insight-tunnel'
    assert value['ProgramArguments'][-1]=='__HOME__/.agentmesh/services/insight-tunnel.sh'
    assert all('/.agentmesh/status/' in value[k] for k in ('StandardOutPath','StandardErrorPath'))
    assert b'TOKEN' not in raw and b'--token' not in raw
