import os
import pytest
from agentmesh_log.control.layout import Layout
from agentmesh_log.control.notify import notifier, read_webhook

URL='https://discord.com/api/webhooks/123/abc-DEF_9'


def hook(tmp_path, text=URL):
    path=tmp_path/'hook'; path.write_text(text+'\n'); path.chmod(0o600); return path


def test_read_webhook_rules(tmp_path):
    assert read_webhook(tmp_path/'missing') is None
    path=hook(tmp_path); assert read_webhook(path)==URL
    if os.name!='nt':
        path.chmod(0o644)
        with pytest.raises(PermissionError): read_webhook(path)
    path=hook(tmp_path,'https://example.com/x')
    with pytest.raises(ValueError): read_webhook(path)


def test_notifier_sends(tmp_path):
    layout=Layout(tmp_path); layout.webhook=hook(tmp_path); sent=[]
    notifier(layout,sender=lambda url,text:sent.append((url,text)))('hello')
    assert sent==[(URL,'hello')]


def test_notifier_swallows_errors(tmp_path, capsys):
    layout=Layout(tmp_path); layout.webhook=hook(tmp_path)
    def down(url, text): raise OSError('down')
    notifier(layout,sender=down)('hello')
    assert '"notify_error": "OSError"' in capsys.readouterr().err
    layout.webhook=tmp_path/'missing'; sent=[]
    notifier(layout,sender=lambda url,text:sent.append(text))('hello')
    assert sent==[]
