"""Resumable local security setup. Proposals are not trust; probes never write production DBs."""
from __future__ import annotations
from contextlib import closing
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import uuid

import memory_sync as sync
import signed_packets as signed

ACK_FORMAT = 'agentmesh-signed-probe-receipt-v1'
ACK_DOMAIN = b'AgentMesh/probe-application-receipt/Ed25519/v1\x00'
ACK_FIELDS = {'format', 'group', 'node', 'sender', 'key_id', 'packet_uuid', 'packet_digest',
              'origin_sender', 'origin_node', 'result', 'committed_at', 'signature'}
STEPS = {'prerequisites': 1, 'identity': 2, 'pairing': 3, 'roundtrip': 4, 'activation': 5, 'active': 6}


def stamp():
    return datetime.now(timezone.utc).isoformat()


def backup(database, destination):
    destination = signed.no_symlinks(destination)
    if destination.exists(): raise ValueError('backup path already exists')
    source = signed.no_symlinks(database)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Exclusive file creation prevents an existing recovery snapshot being overwritten.
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600); os.close(fd)
    try:
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro', uri=True)) as src, closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)
            if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok': raise ValueError('invalid backup')
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination


def inspect(database, exchange, security_dir, state_path):
    database, exchange, security_dir, state_path = map(signed.no_symlinks, (database, exchange, security_dir, state_path))
    for local in (database, security_dir, state_path):
        if local == exchange or local.is_relative_to(exchange): raise ValueError('local state must be outside exchange')
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro', uri=True)) as c:
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA query_only=ON')
        cfg = sync.config(c)
        required = bool(c.execute("SELECT 1 FROM sqlite_master WHERE name='_sync_security'").fetchone())
    accepted = (exchange / '.stfolder').is_dir()
    signed.no_symlinks(exchange / '.stfolder')
    try: signed.crypto(); crypto_ready = True
    except ValueError: crypto_ready = False
    return {'group': cfg['group_id'], 'node': cfg['node'], 'policy': 'required' if required else 'legacy',
            'python_ready': sys.version_info >= (3, 10), 'crypto_ready': crypto_ready, 'exchange_ready': accepted}


def install_crypto():
    # Explicit user option only. Bound runtime and no shell; use this exact interpreter.
    requirements = Path(__file__).with_name('requirements-security.txt')
    result = subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', str(requirements)],
                            capture_output=True, text=True, timeout=180)
    if result.returncode: raise ValueError('crypto installation failed; install pinned requirements with approved tooling')
    signed.crypto()


def load_state(path):
    path = Path(path)
    if not path.exists(): return None
    value = signed.parse(signed.read_local(path, private=True))
    if type(value) is not dict or value.get('format') != 'agentmesh-security-wizard-v1' or value.get('step') not in STEPS:
        raise ValueError('invalid wizard state; explicit recovery required')
    return value


def public_status(state, prerequisites):
    # This is the only projection suitable for telemetry/dashboard consumption.
    state = state or {}
    return {'format': 'agentmesh-security-status-v1', 'policy': prerequisites['policy'],
            'display_name': state.get('display_name'), 'node': prerequisites['node'],
            'pairing': state.get('pairing', 'unknown'), 'wizard_step': state.get('step', 'prerequisites'),
            'next_action': state.get('next_action', 'check_prerequisites'),
            'roundtrip': 'verified' if state.get('roundtrip') else 'pending',
            'roundtrip_scope': 'isolated SQLite probe, not production recall',
            'roundtrip_packet_uuid': (state.get('probe') or {}).get('uuid') if state.get('roundtrip') else None,
            'roundtrip_verified_at': (state.get('roundtrip') or {}).get('verified_at'),
            'prerequisites': {k: prerequisites[k] for k in ('python_ready', 'crypto_ready', 'exchange_ready')}}


def save_state(path, state):
    path = signed.no_symlinks(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    state['updated_at'] = stamp()
    signed.write_local(path, state)


def signed_ack(security, packet, receipt_checksum):
    if receipt_checksum != signed.receipt_digest(packet): raise ValueError('probe receipt mismatch')
    security.check_self()
    ack = {'format': ACK_FORMAT, 'group': security.public['group'], 'node': security.public['node'],
           'sender': security.public['sender'], 'key_id': security.public['key_id'],
           'packet_uuid': packet['uuid'], 'packet_digest': receipt_checksum,
           'origin_sender': packet['sender'], 'origin_node': packet['node'],
           'result': 'isolated_sqlite_probe_committed', 'committed_at': stamp()}
    ack['signature'] = security.key.sign(ACK_DOMAIN + signed.typed(ack)).hex()
    return ack


def verify_ack(security, ack, probe, peer):
    _, Public, InvalidSignature = signed.crypto()
    if type(ack) is not dict or set(ack) != ACK_FIELDS or ack['format'] != ACK_FORMAT or ack['result'] != 'isolated_sqlite_probe_committed':
        raise ValueError('invalid signed application receipt')
    expected = (security.public['group'], peer['node'], peer['sender'], probe['uuid'], probe['digest'], security.public['sender'], security.public['node'])
    actual = tuple(ack[k] for k in ('group', 'node', 'sender', 'packet_uuid', 'packet_digest', 'origin_sender', 'origin_node'))
    if actual != expected: raise ValueError('receipt is for a different peer or challenge')
    trust = signed.read_trust(security.directory)['peers'].get(ack['key_id'])
    if not trust or trust['revoked'] or {k: trust[k] for k in signed.PUBLIC_FIELDS} != peer:
        raise ValueError('receipt signer is not the explicitly approved peer')
    when = datetime.fromisoformat(ack['committed_at'])
    if when.tzinfo is None: raise ValueError('receipt timestamp requires offset')
    try:
        Public.from_public_bytes(signed.hex_bytes(trust['public_key'], 32)).verify(
            signed.hex_bytes(ack['signature'], 64), ACK_DOMAIN + signed.typed({k: v for k, v in ack.items() if k != 'signature'}))
    except InvalidSignature as exc: raise ValueError('invalid signed receipt') from exc
    return ack


def stage_probe(security, exchange):
    import sqlite_memory
    ident = str(uuid.uuid4())
    private = security.directory / 'probes' / ident
    private.mkdir(parents=True, mode=0o700)
    database = private / 'sender.db'
    sqlite_memory.init_database(database)
    sync.initialize(database, security.public['node'], security.public['group'])
    with sync.connect(database) as c, c:
        c.execute('INSERT INTO summary_state(consumer) VALUES(?)', ('agentmesh-security-probe:' + ident,))
    sync.capture(database, security=security)
    with sync.connect(database) as c:
        packet = signed.parse(c.execute('SELECT packet FROM _sync_outbox').fetchone()[0])
    challenge = signed.no_symlinks(Path(exchange) / 'pairing' / 'smoke' / packet['uuid'])
    sync.publish(database, challenge, security=security)
    return {'uuid': packet['uuid'], 'digest': signed.receipt_digest(packet)}


def receive_probes(security, exchange):
    import sqlite_memory
    processed = 0
    root = signed.no_symlinks(Path(exchange) / 'pairing' / 'smoke')
    for challenge in sorted(root.glob('*')):
        if not signed.canonical_uuid(challenge.name): continue
        signed.no_symlinks(challenge)
        for path in sorted((challenge / 'signed-changes').glob('*/*.json')):
            packet = signed.parse(signed.read_local(path))
            security.verify(packet, security.public['group'], path.parent.name)
            if packet['sender'] == security.public['sender']: continue
            if packet['uuid'] != challenge.name or path.name != packet['uuid'] + '.json': raise ValueError('probe identity mismatch')
            # Restrict the smoke channel to one content-free synthetic row.
            body = packet['body']
            if len(body) != 1 or body[0]['table'] != 'summary_state' or body[0]['parent'] is not None or not body[0]['key'][0].startswith('agentmesh-security-probe:'):
                raise ValueError('not an isolated security probe')
            private = security.directory / 'probes' / challenge.name
            private.mkdir(parents=True, exist_ok=True, mode=0o700)
            database = private / 'receiver.db'
            if not database.exists():
                sqlite_memory.init_database(database)
                sync.initialize(database, security.public['node'], security.public['group'])
            sync.receive(database, challenge, security=security)
            with sync.connect(database) as c:
                receipt = c.execute('SELECT checksum FROM _sync_receipts WHERE uuid=?', (packet['uuid'],)).fetchone()
            if not receipt: raise ValueError('remote probe did not commit')
            ack = signed_ack(security, packet, receipt[0])
            destination = signed.no_symlinks(challenge / 'receipts' / (security.public['node'] + '.json'))
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists(): signed.write_local(destination, ack, exclusive=True)
            else:
                # Keep immutable receipt; never convert a delivered file into trust.
                old = signed.parse(signed.read_local(destination))
                if old['packet_digest'] != ack['packet_digest']: raise ValueError('receipt collision')
            processed += 1
    return processed


def resume(database, exchange, security_dir, state_path, *, create_identity=False,
           display_name=None, peer_public=None, confirm_fingerprint=None, publish_proposal=False,
           send_probe=False, accept_probes=False, activate=False, confirm_both_peers=False,
           confirm_legacy_boundary=False, dry_run=False, allow_install_crypto=False):
    prerequisites = inspect(database, exchange, security_dir, state_path)
    state = load_state(state_path)
    bindings = {'database': str(signed.no_symlinks(database)), 'exchange': str(signed.no_symlinks(exchange)),
                'security_dir': str(signed.no_symlinks(security_dir)), 'group': prerequisites['group'], 'node': prerequisites['node']}
    if state and any(state.get(k) != v for k, v in bindings.items()): raise ValueError('wizard scope changed; recovery required')
    if dry_run:
        if state and state.get('sender'):
            state = dict(state)
            try:
                security = signed.Security(security_dir)
                if security.public['sender'] != state['sender'] or (security.public['group'], security.public['node']) != (state['group'], state['node']):
                    raise ValueError('identity mismatch')
                peer = state.get('peer')
                if peer:
                    entry = signed.read_trust(security_dir)['peers'].get(peer['key_id'])
                    if not entry or entry['revoked']:
                        state.update(pairing='revoked' if entry else 'unknown', roundtrip=None, step='pairing', next_action='confirm_peer_fingerprint_out_of_band')
                    elif state.get('probe'):
                        ack_path = Path(exchange) / 'pairing' / 'smoke' / state['probe']['uuid'] / 'receipts' / (peer['node']+'.json')
                        if ack_path.exists():
                            verify_ack(security, signed.parse(signed.read_local(ack_path)), state['probe'], peer)
                        else: state['roundtrip'] = None
            except (OSError, ValueError, TypeError, KeyError):
                state.update(pairing='unknown', roundtrip=None, next_action='recover_identity_or_receipt')
        return public_status(state, prerequisites)
    if not prerequisites['crypto_ready'] and allow_install_crypto:
        install_crypto(); prerequisites = inspect(database, exchange, security_dir, state_path)
    if not all(prerequisites[k] for k in ('python_ready', 'crypto_ready', 'exchange_ready')):
        return public_status(state, prerequisites)
    if state is None:
        if prerequisites['policy'] == 'required' and not Path(security_dir).exists(): raise ValueError('signed database lost identity; explicit recovery required')
        state = {'format': 'agentmesh-security-wizard-v1', **bindings, 'step': 'identity', 'pairing': 'pending',
                 'next_action': 'create_or_reuse_local_identity', 'display_name': None, 'probe': None, 'roundtrip': None}
        # Backup first. This does not change DB, allocation, workflows or scheduler.
        destination = Path(state_path).parent / 'security-backups' / (str(uuid.uuid4()) + '.db')
        state['backup'] = str(backup(database, destination))
        save_state(state_path, state)
    if not Path(security_dir).exists():
        if state.get('sender'): raise ValueError('persistent identity missing; do not auto-generate a replacement')
        if not create_identity:
            save_state(state_path, state); return public_status(state, prerequisites)
        if display_name is None or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}', display_name): raise ValueError('display name must be 1-64 English ASCII characters')
        signed.init_identity(security_dir, prerequisites['group'], prerequisites['node'])
        state['display_name'] = display_name
    security = signed.Security(security_dir)
    if (security.public['group'], security.public['node']) != (prerequisites['group'], prerequisites['node']): raise ValueError('identity/database mismatch')
    if prerequisites['policy'] == 'required':
        with sync.connect(database) as c:
            bound = c.execute('SELECT sender FROM _sync_security').fetchone()
            if bound is None or bound[0] != security.public['sender']: raise ValueError('signed database sender mismatch; recover identity')
    if state.get('sender') and state['sender'] != security.public['sender']: raise ValueError('identity changed; explicit recovery required')
    state['sender'] = security.public['sender']
    if state['step'] == 'identity': state.update(step='pairing', next_action='confirm_peer_fingerprint_out_of_band')
    if publish_proposal:
        proposal = signed.no_symlinks(Path(exchange) / 'pairing' / 'proposals' / (security.public['sender'] + '.json'))
        proposal.parent.mkdir(parents=True, exist_ok=True)
        if not proposal.exists(): signed.write_local(proposal, security.public, exclusive=True)
        elif signed.parse(signed.read_local(proposal)) != security.public: raise ValueError('public proposal collision')
    if peer_public is not None:
        peer = signed.check_public(signed.parse(signed.read_local(peer_public)))
        if peer['sender'] == security.public['sender'] or peer['group'] != prerequisites['group']: raise ValueError('foreign or self peer proposal')
        if confirm_fingerprint is None:
            state['next_action'] = 'confirm_peer_fingerprint_out_of_band'
        else:
            signed.approve(security_dir, peer, confirm_fingerprint, prerequisites['group'], peer['node'], peer['sender'])
            state.update(peer=peer, pairing='approved', step='roundtrip', next_action='send_and_receive_signed_probe')
    peer = state.get('peer')
    if peer:
        entry = signed.read_trust(security_dir)['peers'].get(peer['key_id'])
        if not entry or entry['revoked']:
            state.update(pairing='revoked' if entry else 'unknown', roundtrip=None, step='pairing', next_action='confirm_peer_fingerprint_out_of_band')
    if accept_probes:
        if state['pairing'] != 'approved': raise ValueError('peer must be explicitly approved first')
        receive_probes(security, exchange)
    if send_probe:
        if state['pairing'] != 'approved': raise ValueError('peer must be explicitly approved first')
        if state.get('probe') is None: state['probe'] = stage_probe(security, exchange)
    current_roundtrip_verified = False
    if state.get('probe') and state['pairing'] == 'approved' and peer is not None:
        state['roundtrip'] = None
        ack_path = Path(exchange) / 'pairing' / 'smoke' / state['probe']['uuid'] / 'receipts' / (peer['node'] + '.json')
        if ack_path.exists():
            ack = verify_ack(security, signed.parse(signed.read_local(ack_path)), state['probe'], peer)
            state.update(roundtrip={'verified_at': stamp(), 'committed_at': ack['committed_at']},
                         step='activation', next_action='confirm_coordinated_legacy_boundary')
            if prerequisites['policy'] == 'required':
                state.update(step='active', next_action='restart_worker_with_same_security_directory')
            current_roundtrip_verified = True
    if activate:
        if not current_roundtrip_verified or state['pairing'] != 'approved' or not confirm_both_peers or not confirm_legacy_boundary:
            raise ValueError('activation requires signed probe receipt and explicit both-peer compatibility/legacy-boundary approval')
        # Only operator-confirmed activation writes the production DB latch.
        with sync.connect(database) as c, c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute('SELECT count(*) FROM _sync_diagnostics').fetchone()[0]: raise ValueError('resolve legacy diagnostics before activation')
            signed.require_policy(c, security, exchange)
        state.update(step='active', next_action='restart_worker_with_same_security_directory')
        prerequisites['policy'] = 'required'
    save_state(state_path, state)
    return public_status(state, prerequisites)


def interactive(args):
    # CLI only; all approval requires operator typing, never dashboard buttons.
    print('[1/6] Prerequisites | Python, cryptography, accepted Syncthing folder')
    initial = resume(args.database, args.exchange, args.security_dir, args.state, dry_run=True)
    if not all(initial['prerequisites'].values()):
        print('Pending prerequisites. Install requirements-security.txt with the worker interpreter; resume later.')
        return initial
    create = False; name = None
    if not Path(args.security_dir).exists():
        print('[2/6] Local identity | Existing OS allocation slot; no arbitrary-node allocation')
        create = input('Create a private persistent identity after SQLite backup? Type CREATE: ').strip() == 'CREATE'
        if create: name = input('English ASCII display name: ').strip()
    result = resume(args.database, args.exchange, args.security_dir, args.state, create_identity=create, display_name=name)
    if not Path(args.security_dir).exists(): return result
    security = signed.Security(args.security_dir)
    print('Local fingerprint (full SHA-256): ' + security.public['key_id'])
    print('[3/6] Pairing | Proposals remain untrusted until independently confirmed')
    proposal = input('Publish public-only proposal? Type PUBLISH or Enter to skip: ').strip() == 'PUBLISH'
    peer_file = input('Peer public proposal file, or Enter to stay pending: ').strip()
    fingerprint = None
    if peer_file:
        peer = signed.check_public(signed.parse(signed.read_local(peer_file)))
        print('Proposed scope | Group ' + peer['group'] + ' | Slot ' + peer['node'] + ' | Sender ' + peer['sender'])
        fingerprint = input('Full peer fingerprint confirmed out of band, or Enter to stay pending: ').strip() or None
    result = resume(args.database, args.exchange, args.security_dir, args.state, publish_proposal=proposal,
                    peer_public=peer_file or None, confirm_fingerprint=fingerprint)
    if result['pairing'] != 'approved': return result
    print('[4/6] Signed roundtrip | Isolated SQLite probe requires a remote signed committed receipt')
    if input('Send local probe and process approved peer probes? Type PROBE: ').strip() == 'PROBE':
        result = resume(args.database, args.exchange, args.security_dir, args.state, send_probe=True, accept_probes=True)
    if result['roundtrip'] != 'verified':
        print('Pending remote application receipt. Peer must approve your fingerprint and resume its probe step.')
        return result
    print('[5/6] Upgrade boundary | Stop/coordinate all peers; drain unsigned history; preserve backups')
    both = input('All participating peers support signing and are coordinated? Type BOTH: ').strip() == 'BOTH'
    boundary = input('Every legacy packet applied on all peers and writers paused? Type DRAINED: ').strip() == 'DRAINED'
    activate = input('Enable REQUIRED policy on this database? Type ACTIVATE: ').strip() == 'ACTIVATE'
    if activate:
        result = resume(args.database, args.exchange, args.security_dir, args.state,
                        activate=True, confirm_both_peers=both, confirm_legacy_boundary=boundary)
    print('[6/6] ' + ('Required policy configured; restart with the same identity.' if result['policy'] == 'required' else 'Legacy policy preserved; activation is still pending.'))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__import__('brand').description(__doc__), formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ('database', 'exchange', 'security-dir', 'state'): parser.add_argument('--' + name, required=True)
    parser.add_argument('action', choices=('resume', 'status'), default='resume', nargs='?')
    parser.add_argument('--interactive', action='store_true'); parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--create-identity', action='store_true'); parser.add_argument('--display-name')
    parser.add_argument('--publish-proposal', action='store_true'); parser.add_argument('--peer-public'); parser.add_argument('--confirm-fingerprint')
    parser.add_argument('--send-probe', action='store_true'); parser.add_argument('--accept-probes', action='store_true')
    parser.add_argument('--activate', action='store_true'); parser.add_argument('--confirm-both-peers', action='store_true')
    parser.add_argument('--confirm-legacy-boundary', action='store_true'); parser.add_argument('--install-crypto', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.interactive and args.action == 'resume' and not args.dry_run:
            from contextlib import redirect_stdout
            with redirect_stdout(sys.stderr): result = interactive(args)
        else:
            result = resume(args.database, args.exchange, args.security_dir, args.state,
                create_identity=args.create_identity, display_name=args.display_name, peer_public=args.peer_public,
                confirm_fingerprint=args.confirm_fingerprint, publish_proposal=args.publish_proposal,
                send_probe=args.send_probe, accept_probes=args.accept_probes, activate=args.activate,
                confirm_both_peers=args.confirm_both_peers, confirm_legacy_boundary=args.confirm_legacy_boundary,
                dry_run=args.dry_run or args.action == 'status', allow_install_crypto=args.install_crypto)
        print(json.dumps(result, sort_keys=True))
        ready = result['policy'] == 'required' and result['wizard_step'] == 'active' and result['pairing'] == 'approved' and all(result['prerequisites'].values())
        return 0 if ready or args.action == 'status' or args.dry_run else 2
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, subprocess.SubprocessError, EOFError):
        print('Wizard blocked. Check identity/scope/approval/receipt and current policy before recovery; no automatic fallback.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
