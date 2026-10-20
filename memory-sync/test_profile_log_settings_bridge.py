"""Settings renderer compatibility, using only a disposable file-key-store home."""
import json
import os
import sys
from pathlib import Path
import pytest
pytest.importorskip('pyrage')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'profile-log'))
from agentmesh_log.apply import apply_settings
from agentmesh_log.broker.keystore import BrokerHome, BrokerState, FileKeyStore
from agentmesh_log.settings import save_user
from agentmesh_log.presence import ConsolePresence
from profile_log_bridge import load_config, FIELDS


def test_s8_settings_bridge(tmp_path, monkeypatch):
    root=tmp_path/'agentmesh'; root.mkdir(mode=0o700); monkeypatch.setenv('AGENTMESH_HOME',str(root))
    monkeypatch.delenv('AGENTMESH_PROFILE_LOG_BRIDGE',raising=False)
    home=BrokerHome(root/'profile-log'); store=FileKeyStore(home); store.save(BrokerState.create(['work']))
    db=root/'synthetic.db'
    save_user({'agents':{'recall':{'read':['work/common']},'importer':{'write':['work/common'],'allow_import':True}},
               'migration':{'global':'work/common','null_project':'work/common','projects':{},'prefixes':[]},
               'bridge':{'enabled':True,'profile':'work','recall_agent':'recall','import_agent':'importer'}})
    local=root/'settings.local.json'
    local.write_text(json.dumps({'bridge':{'database':str(db),'authority':True,'cli_argv':[sys.executable,'-m','agentmesh_log.cli']}}),encoding='utf-8'); local.chmod(0o600)
    console=ConsolePresence(input_fn=lambda _:'apply',isatty=lambda:True,output=lambda _:None)
    assert apply_settings(console=console,output=lambda _:None)['ok']
    value=load_config(db)
    assert value is not None and set(value)==FIELDS
    assert value['agent_key']==str(root/'agents/recall.key') and value['import_agent_key']==str(root/'agents/importer.key')
    assert value['mapping']==str(root/'migration-mapping.json') and value['authority'] is True
    if os.name!='nt': assert (root/'synthetic.db.profile-log.json').stat().st_mode & 0o777==0o600
    save_user({'bridge':{'enabled':False}})
    assert apply_settings(output=lambda _:None)['ok'] and load_config(db)['enabled'] is False
