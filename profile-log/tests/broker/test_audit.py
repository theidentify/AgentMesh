import json
from agentmesh_log.broker.audit import Audit


def test_audit(tmp_path):
    a=Audit(tmp_path/'audit',b'a'*32)
    a.append(agent_id=None,op='handshake',outcome='unauthenticated')
    row=json.loads((tmp_path/'audit').read_bytes())
    assert row['agent_id'] is None and row['outcome']=='unauthenticated'
    assert set(row)==set('ts agent_id op outcome profiles scopes cross_profile query_hmac query_chars result_count result_ids redactions label'.split())
