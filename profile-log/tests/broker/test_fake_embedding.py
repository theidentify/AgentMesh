import pytest
from .support import FakeEmbedding


def test_fake_synonyms_normalized():
    e=FakeEmbedding(); texts=['database','ฐานข้อมูล','deploy','unrelated']
    vectors=e.embed(texts)
    assert vectors[0]==vectors[1] and vectors[0]!=vectors[2]
    assert all(sum(x*x for x in v)==pytest.approx(1) for v in vectors)
    assert e.calls==[texts] and all(v[-1]>0 for v in vectors)
    assert e.space==dict(provider='fake',model='fake',revision='f'*64,metric='cosine')
    assert FakeEmbedding(revision='a'*64).space['revision']=='a'*64


def test_fake_failure():
    e=FakeEmbedding(fail=True)
    with pytest.raises(RuntimeError,match='no fallback'): e.embed(['database'])
    assert e.calls==[]
