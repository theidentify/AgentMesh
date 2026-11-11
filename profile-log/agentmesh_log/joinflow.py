"""``workspace join-link``: the member's side of one-click join, as one resumable command.

Each run moves the join as far as it can and records where it stands in ``<home>/joins.json`` (content-free, for
the client). A person approves once per run, with a summary of everything the run may do; later steps of the same
run reuse that approval (each one is still logged in ``presence.jsonl``). Run it again to continue after waiting.

    requested -> waiting_for_admin -> syncing -> ready        (or rejected / expired / failed)
"""
import argparse
import json
import time
from pathlib import Path
from .registry_store import now


class Bundled:
    """One typed approval for the steps this run summarised; anything else is refused."""
    ACTIONS = frozenset(('trust', 'grant', 'open'))

    def __init__(self, console): self.console, self.ok = console, False

    def approve(self, summary):
        self.ok = self.console is not None and self.console.confirm('trust', summary)
        return self.ok

    def confirm(self, action, summary): return self.ok and action in self.ACTIONS


def _status(home, entry, **extra):
    out = {k: entry[k] for k in ('workspace', 'label', 'role', 'state', 'reason') if k in entry}
    return dict(out, home=str(home.root), **extra)


def _remote_args(home):
    from .cli import home_remote
    return home_remote(argparse.Namespace(filesystem=None, s3_bucket=None, s3_prefix=None), home)


def _sync(home, store):
    from .broker.service import Broker
    from .cli import remote_storage
    broker = Broker(home, store=store)
    try: return broker.sync_once(remote_storage(_remote_args(home)))
    finally: broker.close()


def _membership(home, store, token):
    """(role, scope names) once this account is a member and the invite's scopes are on this device; else (role, None)."""
    from . import workspace
    from .broker.grants import names
    from .cli import registry_of
    from .storage.filesystem import FilesystemStorage
    local = FilesystemStorage(home.root / 'replica'); state = store.load()
    registry = registry_of(local, state)
    owner = registry.profile_owner(token['workspace']) if hasattr(registry, 'profile_owner') else token['owner_account']
    ws = workspace.load(local, token['workspace'], registry, owner)
    role = ws.role(state.account.account_id, now()) if ws.exists else None
    invite = ws.state.invites.get(token['invite']) if ws.exists else None
    if role is None or invite is None: return role, None
    known = names(state)
    if not all(sid in known for sid in invite['scopes']): return role, None
    return role, sorted(known[sid] for sid in invite['scopes'])


def _write_remote(home, token, folder, creds, ref):
    from .broker.keystore import atomic_write
    remote = token['remote']
    if remote['kind'] == 'filesystem':
        if folder is None: raise ValueError('invalid_request')   # this workspace syncs through a shared folder
        value = {'filesystem': str(Path(folder).resolve()), 'layout': 'flat'}
    else:
        if creds is None: raise ValueError('denied')   # an S3 workspace must hand over a credential
        value = dict(s3_bucket=remote['bucket'], s3_prefix=remote['prefix'], s3_endpoint=remote['endpoint'], s3_credentials=ref,
                     s3_credentials_kind=creds['kind'], s3_region=remote['region'], s3_path_style=remote['path_style'], layout='flat')
    atomic_write(Path(home.root) / 'remote.json', json.dumps(value, sort_keys=True).encode('utf-8'))


def join_link(args, *, backend=None, console=None, opener=None, store_creds=None, sleep=time.sleep, clock=time.monotonic,
              rules_root=None):
    from . import invites, joinlink, settings, federation
    from .broker.keystore import BrokerHome
    from .oskeystore import select_store, initialize
    from .presence import PresenceManager
    from .storage.filesystem import FilesystemStorage
    from .storage import r2creds
    token = invites.decode_link(Path(args.link_file).read_text(encoding='utf-8'))
    ws_id = token.get('workspace')
    if token['format'] != invites.TOKEN_V2: raise ValueError('invalid_request')   # v1 tokens: workspace join --invite
    root = Path(args.home) if args.home else settings.root() / 'ws' / ws_id[:8]
    home = BrokerHome(root)
    entry = next((e for e in joinlink.read_joins(home) if e.get('workspace') == ws_id), None)
    if entry is not None and entry['state'] in ('ready', 'rejected', 'failed'): return _status(home, entry)
    bundled = Bundled(console)
    agent_name = args.agent_name or 'workspace-' + ws_id[:8]
    if entry is None:
        if now() > token['expires_at']:
            return _status(home, joinlink.write_join(home, dict(workspace=ws_id, label=token['label'], role=token['role'],
                           invite=token['invite'], state='expired', reason='link expired', recovery_saved=False)))
        summary = (f"Join workspace {token['label'] or ws_id} as {token['role']}.\n"
                   f"Owner account {token['owner_account']}, fingerprint {token['owner_fingerprint']}: check it with the admin.\n"
                   f"This creates the home {root}, sends a join request, and once an admin approves: syncs, creates the agent "
                   f"'{agent_name}' for the invited scopes and opens its first access.")
        if not bundled.approve(summary): raise ValueError('presence_required')
        fresh = not ((root / 'keystore.json').exists() or (root / 'keystore.sealed').exists())
        recovery = None
        if fresh:
            from .recovery import write_shares
            shares = initialize(home, ['local'], backend=backend)
            recovery = Path(args.recovery_out) if args.recovery_out else settings.root() / 'recovery-pending' / ws_id[:8]
            write_shares(shares, recovery, home.root, home.root / 'replica')
        store = select_store(home, backend); local = FilesystemStorage(home.root / 'replica')
        federation.publish_account(local, token['owner_record'])   # lets the fingerprint check run before any sync
        from .agekeys import DeviceAgeIdentity
        from .cli import execute_federation
        with home.lock():
            state = store.load()
            execute_federation(argparse.Namespace(action='join', invite=None, invite_record=token, profile_id=None,
                                                  owner_account=None, owner_fingerprint=None),
                               home, store, PresenceManager(home, store), local, state, bundled)
            state = store.load(); identity = DeviceAgeIdentity()
            state.control.setdefault('one_click', {})[ws_id] = dict(answer_identity=identity.secret(), slot=token['slot'])
            store.save(state)
            data = joinlink.build_request(store.load(), local, token, identity.recipient)
        channel = joinlink.send_request(token, data, folder=args.folder, request_out=args.request_out, opener=opener)
        entry = joinlink.write_join(home, dict(workspace=ws_id, label=token['label'], role=token['role'], invite=token['invite'],
                                               state='waiting_for_admin', channel=channel, reason=None,
                                               projects=[str(Path(p).resolve()) for p in (args.project or [])],
                                               recovery_saved=not fresh, recovery_out=str(recovery) if recovery else None))
    store = select_store(home, backend)
    deadline = clock() + max(0.0, float(args.wait))
    while True:
        if entry['state'] == 'waiting_for_admin':
            from .agekeys import DeviceAgeIdentity
            data = joinlink.fetch_answer(token, entry['channel'], folder=args.folder, answer_file=args.answer_file, opener=opener)
            if data is not None:
                identity = DeviceAgeIdentity.parse(store.load().control['one_click'][ws_id]['answer_identity'])
                verdict, reason, creds = joinlink.open_answer(data, ws_id, token['slot'], identity)
                if verdict == 'rejected':
                    return _status(home, joinlink.write_join(home, dict(entry, state='rejected', reason=reason or 'rejected')))
                ref = None
                if creds is not None:
                    ref = r2creds.default_ref('org.agentmesh.ws-' + ws_id[:8])
                    (store_creds or r2creds.store_credentials)(ref, (creds['access_key_id'], creds['secret_access_key']), kind=creds['kind'])
                _write_remote(home, token, args.folder, creds, ref)
                entry = joinlink.write_join(home, dict(entry, state='syncing'))
            elif now() > token['expires_at']:
                return _status(home, joinlink.write_join(home, dict(entry, state='expired', reason='no answer before the link expired')))
        if entry['state'] == 'syncing':
            with home.lock():
                _sync(home, store)
                role, scopes = _membership(home, store, token)
                if scopes is not None:
                    return _finish(home, store, token, entry, role, scopes, agent_name, bundled, console, args, rules_root)
        if clock() >= deadline: return _status(home, entry, note='run join-link again to continue (or pass --wait)')
        sleep(min(5.0, max(0.0, deadline - clock())))


def _finish(home, store, token, entry, role, scopes, agent_name, bundled, console, args, rules_root):
    """Create the workspace agent (read and write for members, write-only for contributors), open its first access,
    install the workspace rules. Needs presence: this run's approval, or a fresh one when resuming."""
    from . import joinlink
    from .admin import create_agent
    from .presence import PresenceManager
    if not bundled.ok and not bundled.approve(
            f"Finish joining {token['label'] or token['workspace']}: create the agent '{agent_name}' for "
            f"{', '.join(scopes)} and open its first access."):
        raise ValueError('presence_required')
    presence = PresenceManager(home, store)
    presence.require('agent', 'grant', console=bundled, one_shot=True,
                     summary=f"Create agent '{agent_name}' for {', '.join(scopes)}.")
    state = store.load()
    reads = scopes if role != 'contributor' else []
    path, _ = create_agent(home, state, agent_name, reads, scopes)
    store.save(state)
    presence.open('first_access', console=bundled)
    entry = dict(entry, state='ready', agent_key=str(path), scopes=scopes)
    rules = None
    if not args.no_rules:
        from . import agentrules
        rules = agentrules.register(dict(workspace=token['workspace'], label=token['label'], role=role, home=str(home.root),
                                         agent_key=str(path), scopes=scopes,
                                         projects=sorted(set(entry.get('projects', [])) | {str(Path(p).resolve()) for p in (args.project or [])})),
                                    root=rules_root)
    return _status(home, joinlink.write_join(home, entry), agent_key=str(path), scopes=scopes, rules=rules,
                   note=f'start its broker: agentmesh-log broker run --home {home.root} --sync-interval 60')
