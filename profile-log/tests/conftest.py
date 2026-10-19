import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from agentmesh_log.model import Account, Profile, ScopeTree, Label
from agentmesh_log.identity import DeviceIdentity, AgentIdentity, Registry


@pytest.fixture
def taxonomy():
    account = Account('Private.Email@Example.test')
    first, second = DeviceIdentity(), DeviceIdentity()
    registry = Registry(account.public_record())
    first_cert = first.certificate(account); registry.add_device(first_cert)
    second_cert = second.certificate(account, issuer=first); registry.add_device(second_cert)
    agents = [AgentIdentity(d, account.account_id) for d in (first, second)]
    work, personal = ScopeTree(Profile('work')), ScopeTree(Profile('personal'))
    work.add(work.root.scope_id, 'common', 'common')
    a = work.add(work.root.scope_id, 'project', 'client-a', Label.internal)
    work.add(work.root.scope_id, 'project', 'client-b')
    personal.add(personal.root.scope_id, 'common', 'common')
    phone = (agents[1], work.keys[a.scope_id].write_key)
    return account, registry, [(first, first_cert), (second, second_cert)], agents, work, personal, phone
