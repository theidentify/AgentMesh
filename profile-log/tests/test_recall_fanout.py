import json
import pytest
from agentmesh_log import cli


def answer(profile,ids,counts):
    return dict(query='q',query_plan={'p':profile},candidate_counts=counts,evidence=[],results=[dict(id=i,profile=profile) for i in ids])


class Client:
    def __init__(self,answers): self.answers,self.requests,self.closed=answers,[],False
    def request(self,request): self.requests.append(request); return self.answers[request['profiles'][0]]
    def close(self): self.closed=True


@pytest.fixture
def client(monkeypatch):
    fake=Client({'work':answer('work',['w1','w2','x'],{'durable':3,'events':1}),'personal':answer('personal',['p1','x'],{'durable':2})})
    monkeypatch.setattr(cli.BrokerClient,'from_file',lambda _:fake)
    return fake


def run(capsys,*extra):
    code=cli.main(['recall','q','--compat','--agent-key','/k',*extra])
    return code,json.loads(capsys.readouterr().out)


def test_fanout_merged(client,capsys):
    code,out=run(capsys,'--per-profile','--profile','work','--profile','personal','--limit','10')
    assert code==0 and client.closed
    assert [r['profiles'] for r in client.requests]==[['work'],['personal']]
    assert all(r['compat'] is True and r['scopes'] is None and r['limit']==10 for r in client.requests)
    # Ranks interleave and a duplicate keeps its first position.
    assert [r['id'] for r in out['results']]==['w1','p1','w2','x']
    assert out['candidate_counts']=={'durable':5,'events':1} and out['evidence']==[] and out['query_plan']=={'p':'work'}


def test_limit_after_merge(client,capsys):
    _,out=run(capsys,'--per-profile','--profile','work','--profile','personal','--limit','3')
    assert [r['id'] for r in out['results']]==['w1','p1','w2']


def test_error_wins(client,capsys):
    client.answers['personal']={'error':'presence_required'}
    code,out=run(capsys,'--per-profile','--profile','work','--profile','personal')
    assert code==1 and out=={'error':'presence_required'}


@pytest.mark.parametrize('extra',[['--per-profile'],['--per-profile','--profile','work','--profile','work'],['--per-profile','--profile','work','--scope','work/common']],ids=['none','duplicate','scope'])
def test_invalid(client,capsys,extra):
    code,out=run(capsys,*extra)
    assert code==1 and out=={'error':'invalid_request'} and client.requests==[]


def test_no_flag(client,capsys):
    client.answers[None]=None
    code,_=run(capsys,'--profile','work')
    assert [r['profiles'] for r in client.requests]==[['work']]
