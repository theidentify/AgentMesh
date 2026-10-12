"""Opt-in CI tests on disposable OS stores, never real login items or targets."""
import os
import sys
import time

import pytest

import os_unlock as ou
from disposable_os_store import disposable_store, security

pytestmark = pytest.mark.skipif(os.environ.get('AGENTMESH_NATIVE_KEYCHAIN_TESTS') != '1',
                                reason='native credential tests are CI-only and require explicit opt-in')
KEY_ID = 'c' * 64


@pytest.fixture
def backend(tmp_path):
    if sys.platform != 'darwin' and os.name != 'nt':
        pytest.skip('native backend requires Mac or Windows')
    with disposable_store(tmp_path) as backend:
        try:
            yield backend
        finally:
            if os.name == 'nt':
                skipped = sys.exc_info()[0] is pytest.skip.Exception
                try:
                    backend.delete(KEY_ID)
                except ou.OSUnlockError as exc:
                    if not skipped or str(exc) != ou.NOT_REMOVED:
                        raise


def store_or_skip(backend, secret):
    try:
        backend.store(KEY_ID, secret)
    except ou.OSUnlockError as exc:
        if os.name == 'nt' and str(exc) == ou.REFUSED:
            pytest.skip('Credential Manager unavailable in this CI session (recorded)')
        raise


def test_native_roundtrip_replace_delete(backend):
    first, second = os.urandom(32), os.urandom(32)
    store_or_skip(backend, first)
    assert backend.load(KEY_ID) == first
    backend.store(KEY_ID, second)
    assert backend.load(KEY_ID) == second
    backend.delete(KEY_ID)
    backend.delete(KEY_ID)
    with pytest.raises(ou.OSUnlockError, match=ou.MISSING):
        backend.load(KEY_ID)


def test_native_missing(backend):
    # Probe write availability first on Windows so no-logon runners record refusal.
    if os.name == 'nt':
        store_or_skip(backend, os.urandom(32))
        backend.delete(KEY_ID)
    with pytest.raises(ou.OSUnlockError, match=ou.MISSING):
        backend.load(KEY_ID)


@pytest.mark.skipif(sys.platform != 'darwin', reason='Mac locked-keychain test')
def test_native_locked_keychain_is_bounded(backend):
    backend.store(KEY_ID, os.urandom(32))
    security(['lock-keychain', backend.keychain])
    start = time.monotonic()
    with pytest.raises(ou.OSUnlockError) as caught:
        backend.load(KEY_ID)
    assert str(caught.value) in (ou.REFUSED, ou.NO_RESPONSE)
    assert time.monotonic() - start < ou.TIMEOUT + 5, str(caught.value)
