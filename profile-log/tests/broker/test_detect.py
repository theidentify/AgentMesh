import base64
import json
from pathlib import Path
import random
import string
import pytest
from agentmesh_log.broker.detect import RegexDetector, redact, sanitize


def positives():
    rng = random.Random(731)
    token = lambda n: ''.join(rng.choices(string.ascii_letters + string.digits, k=n))
    b64 = lambda s: base64.urlsafe_b64encode(s.encode()).decode().rstrip('=')
    return {'pem': '-----BEGIN ' + 'PRIVATE KEY-----\n'+token(64)+'\n-----END PRIVATE KEY-----',
        'aws': 'AK'+'IA'+token(16).upper(), 'github': 'gh'+'p_'+token(36),
        'github_pat': 'github'+'_pat_'+token(82), 'slack': 'xox'+'b-'+token(40),
        'anthropic': 'sk'+'-ant-'+token(48), 'openai': 'sk'+'-'+token(40),
        'google': 'AI'+'za'+token(35), 'stripe': 'sk'+'_live_'+token(32),
        'jwt': b64('{"alg":"HS256"}')+'.'+b64('{"sub":"synthetic"}')+'.'+token(43),
        'assignment': 'password'+'='+token(32)}


def test_p3_10_secrets():
    detector = RegexDetector()
    for kind, text in positives().items():
        findings = detector.scan(text)
        assert any(f.kind == kind and f.category == 'secret' for f in findings), kind
        clean = redact(text, findings)
        assert text not in clean and '[REDACTED:' in clean
        body, tags, task, label, counts = sanitize({'kind':'fact', 'content':text}, [text], text)
        assert label.name == 'secret' and kind in counts
        assert text not in json.dumps([body, tags, task])
    with pytest.raises(ValueError): sanitize({next(iter(positives().values())): 'x'}, [], None)
    with pytest.raises(ValueError): sanitize({'x'*65: 'y'}, [], None)


def test_p3_10_pii():
    digits = '110170020345'
    thai = digits + str((11-sum(int(v)*(13-i) for i,v in enumerate(digits))%11)%10)
    card = '4' + '0'*14 + '2'  # Independently constructed Luhn-valid synthetic card.
    for text in ('person@example.test', '+66'+'812345678', '081-234-5678', '02-1234-5678', thai, card):
        findings = RegexDetector().scan(text)
        assert any(f.category == 'pii' for f in findings)
        assert redact(text, findings) == text
        assert sanitize(text, [], None)[3].name == 'internal'


def test_p3_10_budget():
    path = Path(__file__).parents[1] / 'fixtures/detector_corpus/benign.jsonl'
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(lines) >= 500
    counts = {k: sum(any(f.category == k for f in RegexDetector().scan(line)) for line in lines)
              for k in ('secret', 'pii')}
    print('Detector benign rates:', {k: f'{v}/{len(lines)} = {v/len(lines):.2%}' for k,v in counts.items()})
    assert counts['secret']/len(lines) <= .01
    assert counts['pii']/len(lines) <= .05


def test_epoch_corpus():
    import re
    path=Path(__file__).parents[1]/'fixtures/detector_corpus/benign.jsonl'
    lines=[json.loads(line) for line in path.read_text().splitlines()]
    timestamps=[line for line in lines if re.search(r'\b1[0-9]{12}\b',line)]
    assert len(timestamps)>=200
    assert any('created_at' in line for line in timestamps)
    assert any(any(f.kind=='thai_id' for f in RegexDetector().scan(line)) for line in timestamps)
