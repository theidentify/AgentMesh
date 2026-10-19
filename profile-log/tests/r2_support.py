"""Test-only R2 configuration and cleanup. Never part of the Storage API."""
from agentmesh_log import ulid

ENV_NAMES = ('AGENTMESH_R2_ENDPOINT', 'AGENTMESH_R2_BUCKET',
             'AGENTMESH_R2_ACCESS_KEY_ID', 'AGENTMESH_R2_SECRET_ACCESS_KEY')


def build_client(env, *, factory=None):
    __tracebackhide__ = True
    if any(not env.get(name, '').strip() for name in ENV_NAMES):
        raise RuntimeError('missing required R2 environment configuration')
    if not env[ENV_NAMES[0]].startswith('https://'):
        raise RuntimeError('R2 endpoint must use HTTPS')
    try:
        if factory is None:
            import boto3
            factory = boto3.client
        client = factory('s3', endpoint_url=env[ENV_NAMES[0]], region_name='auto',
                         aws_access_key_id=env[ENV_NAMES[2]], aws_secret_access_key=env[ENV_NAMES[3]])
    except Exception:
        raise RuntimeError('R2 client initialization failed') from None
    return client, env[ENV_NAMES[1]]


class CleanupError(RuntimeError):
    pass


def cleanup_run(client, bucket, prefix):
    __tracebackhide__ = True
    # Never allow a base namespace, sibling run or partially specified run prefix.
    if type(prefix) is not str or not prefix.startswith('agentmesh-live-tests/'):
        raise CleanupError('unsafe cleanup prefix')
    parts = prefix.split('/')
    if len(parts) != 3 or parts[2] != '': raise CleanupError('unsafe cleanup prefix')
    try: ulid.parse(parts[1])
    except ValueError: raise CleanupError('unsafe cleanup prefix') from None
    try:
        keys = []
        paginator = client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get('Contents', []):
                if not obj['Key'].startswith(prefix):
                    raise CleanupError('cleanup result outside run prefix')
                keys.append({'Key': obj['Key']})
        # Finish listing before deleting, so pagination is not affected by deletions.
        for offset in range(0, len(keys), 1000):
            result = client.delete_objects(Bucket=bucket, Delete={'Objects': keys[offset:offset + 1000], 'Quiet': True})
            if result.get('Errors'): raise CleanupError('R2 cleanup deletion failed')
        remaining = client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1)
        if remaining.get('Contents'): raise CleanupError('R2 cleanup prefix is not empty')
        return len(keys)
    except CleanupError:
        raise
    except Exception:
        raise CleanupError('R2 cleanup failed') from None
