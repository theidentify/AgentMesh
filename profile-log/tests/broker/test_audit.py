import json
from agentmesh_log.broker.audit import Audit


def test_audit(tmp_path):
    a=Audit(tmp_path/'audit',b'a'*32)
    a.append(agent_id=None,op='handshake',outcome='unauthenticated')
    row=json.loads((tmp_path/'audit').read_bytes())
    assert row['agent_id'] is None and row['outcome']=='unauthenticated'
    assert set(row)==set('ts agent_id op outcome profiles scopes cross_profile query_hmac query_chars result_count result_ids redactions label'.split())

import os
import stat
import pytest
from agentmesh_log.broker.audit import append_row


def test_append_row_owner_only(tmp_path):
    append_row(tmp_path/'rows',{'op':'x'}); append_row(tmp_path/'rows',{'op':'y'})
    assert [json.loads(line)['op'] for line in (tmp_path/'rows').read_bytes().splitlines()]==['x','y']
    if os.name!='nt': assert stat.S_IMODE((tmp_path/'rows').stat().st_mode)==0o600


def test_append_row_refuses_symlink(tmp_path):
    if os.name=='nt': return
    (tmp_path/'target').write_bytes(b''); (tmp_path/'rows').symlink_to(tmp_path/'target')
    with pytest.raises(Exception): append_row(tmp_path/'rows',{'op':'x'})
    assert (tmp_path/'target').read_bytes()==b''
