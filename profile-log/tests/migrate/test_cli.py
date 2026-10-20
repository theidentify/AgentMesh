from agentmesh_log.cli import parser


def test_phase5_cli():
    assert parser().parse_args(['migrate','plan','--db','/synthetic.db']).action=='plan'
    assert parser().parse_args(['recall','synthetic','--compat','--agent-key','/agent']).compat
    assert parser().parse_args(['broker','init','--home','/tmp/test','--profile','work','--file-keystore']).file_keystore


def test_file_keystore_disposable(tmp_path,monkeypatch):
    from agentmesh_log.cli import execute
    from agentmesh_log.broker.keystore import FileKeyStore,BrokerHome
    monkeypatch.setenv('AGENTMESH_ALLOW_FILE_KEYSTORE','1')
    args=parser().parse_args(['broker','init','--home',str(tmp_path/'home'),'--profile','work','--file-keystore'])
    result=execute(args)
    assert len(result['recovery_shares'])==3
    assert FileKeyStore(BrokerHome(tmp_path/'home')).load().record()['account_private_key'] is None
