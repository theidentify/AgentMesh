"""Shared administrative primitives. Callers own locking, presence and saving."""
import os
from pathlib import Path
from .encoding import wire
from .identity import AgentIdentity
from .model import Label
from .broker.grants import Grant, resolve, names
from .broker.keystore import atomic_write


def create_scope(home, state, name, label=Label.general):
    scope = state.add(name, label)
    if state.control.get('phase4'):
        from .keyring import initialize_keyring
        from .storage.filesystem import FilesystemStorage
        from .recovery import publish_history
        storage = FilesystemStorage(home.root / 'replica')
        initialize_keyring(state, storage)
        publish_history(state, storage, state.registry().current_recovery())
    return scope


def make_grant(state, agent_id, public_key, reads, writes, default_write=None,
               allow_secret=False, allow_cross_profile=False, allow_import=False, *, declared=False):
    reads = [resolve(state, n) for n in reads]; writes = [resolve(state, n) for n in writes]
    scope_names = names(state)
    candidates = sorted((s for s in writes if not scope_names[s].endswith('/common')),
                        key=lambda s: (-scope_names[s].count('/'), scope_names[s]))
    default = resolve(state, default_write) if default_write else (None if declared else (candidates[0] if candidates else None))
    if (default is not None and default not in writes) or (not declared and default not in writes):
        raise ValueError('invalid default write')
    selection = [default] if default else sorted(set(reads + writes), key=lambda s: scope_names[s])
    profile = next((t.root.scope_id for s in selection for t in state.trees if s in t.scopes), state.trees[0].root.scope_id)
    return Grant(agent_id, public_key, reads, writes, default, profile, allow_secret, allow_cross_profile, allow_import)


def create_agent(home, state, name, reads, writes, default_write=None,
                 allow_secret=False, allow_cross_profile=False, allow_import=False, *, path=None, declared=False):
    if not name: raise ValueError('invalid name')
    agent = AgentIdentity(state.device, state.account.account_id)
    grant = make_grant(state, agent.agent_id, agent.public_bytes.hex(), reads, writes, default_write,
                       allow_secret, allow_cross_profile, allow_import, declared=declared)
    data = wire(dict(format='agentmesh-agent-key-v1', agent_cert=agent.certificate(),
                     agent_private_key=agent.private_bytes().hex(), device_public_key=state.device.public_bytes.hex(),
                     address=home.address(state.device.device_id)))
    if path is None:
        path = home.root / 'agents' / (agent.agent_id + '.key'); atomic_write(path, data)
    else:
        path = Path(path)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
    state.grants[agent.agent_id] = grant
    return path, grant
