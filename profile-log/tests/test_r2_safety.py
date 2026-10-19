"""Offline tests for the opt-in live harness; no real credentials or network."""
import pytest
from moto import mock_aws
from agentmesh_log import ulid
from r2_support import build_client, cleanup_run

ENV = {
    'AGENTMESH_R2_ENDPOINT': 'https://example.r2.cloudflarestorage.com',
    'AGENTMESH_R2_BUCKET': 'live-harness-test',
    'AGENTMESH_R2_ACCESS_KEY_ID': 'dummy-id',
    'AGENTMESH_R2_SECRET_ACCESS_KEY': 'dummy-secret',
}


@pytest.mark.parametrize('missing', list(ENV), ids=['endpoint', 'bucket', 'id', 'secret'])
def test_missing_env(missing):
    env = dict(ENV); del env[missing]
    def forbidden(*args, **kwargs): pytest.fail('client constructed with missing configuration')
    with pytest.raises(RuntimeError, match='required R2 environment'):
        build_client(env, factory=forbidden)


def test_explicit_credentials():
    calls = []
    def factory(*args, **kwargs): calls.append((args, kwargs)); return object()
    env = dict(ENV, AWS_ACCESS_KEY_ID='wrong-id', AWS_SECRET_ACCESS_KEY='wrong-secret')
    client, bucket = build_client(env, factory=factory)
    assert bucket == ENV['AGENTMESH_R2_BUCKET']
    assert calls == [(('s3',), dict(endpoint_url=ENV['AGENTMESH_R2_ENDPOINT'], region_name='auto',
        aws_access_key_id='dummy-id', aws_secret_access_key='dummy-secret'))]


def test_endpoint_and_error_redaction():
    def forbidden(*args, **kwargs): pytest.fail('insecure endpoint reached client')
    with pytest.raises(RuntimeError, match='HTTPS'):
        build_client(dict(ENV, AGENTMESH_R2_ENDPOINT='http://example.test'), factory=forbidden)
    def failure(*args, **kwargs): raise ValueError('dummy-id dummy-secret')
    with pytest.raises(RuntimeError) as caught: build_client(ENV, factory=failure)
    assert 'dummy-id' not in str(caught.value) and 'dummy-secret' not in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize('prefix', ['', 'other/run/', 'agentmesh-live-tests/',
    'agentmesh-live-tests/not-a-ulid/', 'agentmesh-live-tests/' + '0'*26],
    ids=['empty', 'other', 'base', 'invalid', 'slash'])
def test_cleanup_prefix_guard(prefix):
    class Forbidden:
        def get_paginator(self, *args): pytest.fail('unsafe cleanup reached I/O')
    with pytest.raises(RuntimeError, match='unsafe cleanup prefix'):
        cleanup_run(Forbidden(), 'bucket', prefix)


def test_cleanup_batches_and_isolation():
    import boto3
    with mock_aws():
        client = boto3.client('s3', region_name='us-east-1', aws_access_key_id='testing',
                              aws_secret_access_key='testing')
        client.create_bucket(Bucket='live-harness-test')
        prefix = f'agentmesh-live-tests/{ulid.generate()}/'
        outside = f'agentmesh-live-tests/{ulid.generate()}/keep'
        client.put_object(Bucket='live-harness-test', Key=outside, Body=b'keep')
        for i in range(1001):
            client.put_object(Bucket='live-harness-test', Key=prefix + str(i), Body=b'data')
        batches = []
        def record(params, **kwargs): batches.append(len(params['Delete']['Objects']))
        client.meta.events.register('before-parameter-build.s3.DeleteObjects', record)
        assert cleanup_run(client, 'live-harness-test', prefix) == 1001
        assert batches == [1000, 1]
        assert client.list_objects_v2(Bucket='live-harness-test')['KeyCount'] == 1
        assert client.get_object(Bucket='live-harness-test', Key=outside)['Body'].read() == b'keep'


def test_cleanup_refuses_foreign_result():
    class Client:
        def get_paginator(self, name): return self
        def paginate(self, **kwargs): return [{'Contents': [{'Key': 'outside'}]}]
        def delete_objects(self, **kwargs): pytest.fail('foreign key deletion attempted')
    with pytest.raises(RuntimeError, match='outside run prefix'):
        cleanup_run(Client(), 'bucket', f'agentmesh-live-tests/{ulid.generate()}/')


def test_cleanup_error_redaction():
    class Client:
        def get_paginator(self, name): raise RuntimeError('dummy-id dummy-secret')
    with pytest.raises(RuntimeError) as caught:
        cleanup_run(Client(), 'bucket', f'agentmesh-live-tests/{ulid.generate()}/')
    assert 'dummy-id' not in str(caught.value) and 'dummy-secret' not in str(caught.value)
    assert caught.value.__suppress_context__


def test_live_harness_offline(taxonomy, monkeypatch, capsys):
    import boto3
    import test_r2_live as live
    with mock_aws():
        client = boto3.client('s3', region_name='us-east-1', aws_access_key_id='testing',
                              aws_secret_access_key='testing')
        client.create_bucket(Bucket='live-harness-test')
        monkeypatch.setattr(live, 'build_client', lambda env: (client, 'live-harness-test'))
        finalizers = []
        class Request:
            def addfinalizer(self, fn): finalizers.append(fn)
        run = live.r2_run.__wrapped__(Request())
        try:
            live.test_contract(run)
            live.test_identical_reput(run)
            live.test_encrypted_events(run, taxonomy)
        finally:
            finalizers[0]()
        assert client.list_objects_v2(Bucket='live-harness-test')['KeyCount'] == 0
        summary = capsys.readouterr().out
        assert summary.startswith('puts=11 gets=5 lists=5 events=3 deleted=10 elapsed=')
        assert len(summary.splitlines()) == 1


def test_enabled_missing_env_fails():
    import os
    from pathlib import Path
    import subprocess
    import sys
    from r2_support import ENV_NAMES
    env = dict(os.environ, AGENTMESH_R2_LIVE='1')
    for name in ENV_NAMES: env.pop(name, None)
    # Missing configuration fails before client construction: no network is possible.
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                             str(Path(__file__).with_name('test_r2_live.py'))],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 1
    assert 'missing required R2 environment configuration' in result.stdout
    assert '3 errors' in result.stdout and 'skipped' not in result.stdout
