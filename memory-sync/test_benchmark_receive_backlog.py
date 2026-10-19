"""The benchmark always constructs disposable data, including chain fixtures."""
import pytest
from support.benchmark_receive_backlog import run


@pytest.mark.parametrize('mode',['legacy','indexed','v2'])
def test_chain_benchmark(mode):
    result=run(10,5,10,mode,chain=True)
    assert result['report']['applied']==5
    assert result['shape']=='chain'
    assert result['diagnostic_writes']==0
    assert result['apply_attempts']<=10
    assert result['indexed_attempts']<=15
