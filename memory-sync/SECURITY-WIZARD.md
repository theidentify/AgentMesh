# Security setup wizard

The CLI wizard is opt-in and resumable. It implements private identity, explicit
fingerprint pairing, an isolated signed SQLite probe and an operator-approved
upgrade boundary. It does not install Syncthing/Python, approve peers from exchange
files, allocate arbitrary node ranges, publish a real package, or start a service.
Read [SIGNED-SYNC.md](SIGNED-SYNC.md) before activation.

## Existing installer

After a deliberately rebuilt private bootstrap package reaches a host:

```sh
PYTHON bootstrap_windows.py --exchange EXCHANGE --local-dir LOCAL --node windows --security-wizard
PYTHON bootstrap_windows.py --exchange EXCHANGE --local-dir LOCAL --node windows --wizard-status
PYTHON bootstrap_windows.py --exchange EXCHANGE --local-dir LOCAL --node windows --wizard-dry-run
```

The cross-platform Python installer can use `--node mac` or `linux` as well. Existing
launchers keep legacy behavior unless this opt-in is requested. Pending setup exits
2 without starting a worker; blocked input exits 1; status/dry-run exit 0. The wizard
prints numbered English ASCII steps and requires exact operator confirmations.
Regular signed restarts reuse the recorded security directory in `data/runtime.json`;
there is no automatic key generation, rotation or repeat pairing. Missing/mismatched
identity blocks. Existing workflow settings and IDs are preserved. A standalone
activation requires explicitly configuring subsequent worker invocations with
`--security-dir`; it does not edit service definitions.

The installer first verifies/imports the existing bootstrap baseline. The manifest's
group must match the existing database: the wizard reads that group, it never
invents another group. Run status/dry-run only against an existing installation;
these options do not extract a package, create keys, or back up a DB.

## Standalone / upgrade

Run from `memory-sync`. Define the same four paths for every command (all local
state, identity and DB must be outside exchange):

```sh
PYTHON security_wizard.py --database DB --exchange EXCHANGE --security-dir KEYS --state STATE status
PYTHON security_wizard.py --database DB --exchange EXCHANGE --security-dir KEYS --state STATE resume --dry-run
PYTHON security_wizard.py --database DB --exchange EXCHANGE --security-dir KEYS --state STATE resume --interactive
```

Prerequisites are Python 3.10+, `cryptography` in the actual worker interpreter and
an accepted `.stfolder` directory. Missing crypto prints pending status rather than
using checksums. Install with `PYTHON -m pip install -r requirements-security.txt`,
or the operator's approved `uv` command. Standalone `--install-crypto` explicitly
opts into a bounded (180-second), shell-free pip subprocess; failure does not hide
behind a stdlib fallback. Python and Syncthing installation remains manual.

An upgrade creates a SQLite backup with the backup API before identity/pairing.
It never copies a live SQLite/WAL or overwrites a prior backup. The private wizard
state records scope and progress. A resumed session refuses changed paths, group,
slot or persistent sender. Once a sender is recorded, a lost identity cannot be
re-created with `--create-identity`; restore it or plan explicit recovery.

Scripted/operator resumptions:

```sh
# Explicit initial private identity; the DB's existing slot/group are reused.
PYTHON security_wizard.py --database DB --exchange EXCHANGE --security-dir KEYS --state STATE resume --create-identity --display-name "Workstation Mac" --publish-proposal
# View your full fingerprint with signed_packets.py; exchange PUBLIC proposals only.
# Peer fingerprint must be independently confirmed, not copied blindly from proposal.
PYTHON security_wizard.py --database DB --exchange EXCHANGE --security-dir KEYS --state STATE resume --peer-public PEER_PUBLIC_JSON --confirm-fingerprint FULL_SHA256
# Repeat explicit trust on the peer, then each peer sends/processes probes.
PYTHON security_wizard.py --database DB --exchange EXCHANGE --security-dir KEYS --state STATE resume --send-probe --accept-probes
# Resume after the peer's receipt has actually arrived.
PYTHON security_wizard.py --database DB --exchange EXCHANGE --security-dir KEYS --state STATE resume
```

Proposals are public keys under `pairing/proposals/`, never trusted automatically.
Display names are presentation only, English ASCII, and do not confer authority or
new ID ranges. The legacy three-slot limitation remains: do not add a second Mac
writer under a different display name. Each receiving operator approves the full
fingerprint and group/sender/slot binding. No dashboard trust button is provided.

## Signed roundtrip evidence

`--send-probe` creates a new **empty disposable DB** outside exchange, inserts one
synthetic `summary_state` row and signs its packet. It is published only under
`pairing/smoke/<packet UUID>/signed-changes/`, not the production namespace.
`--accept-probes` verifies pinned authorization/signature and applies that packet to
another disposable DB. Only after its exact `_sync_receipts` row is read back from
committed SQLite state does the peer sign a receipt. The receipt binds the original
sender, group, source slot, exact packet UUID and authenticated packet digest, peer
identity, result and commit timestamp with its own domain-separated Ed25519 signature.
The origin verifies this against its explicitly pinned peer and persisted challenge.
A forged status JSON, unrelated packet receipt, file presence or Syncthing 100% cannot
mark the roundtrip verified. Lost ACK delivery can be retried without duplicate apply.

This demonstrates remote protocol/dependency/SQLite probe execution, **not production
memory recall, Windows ACLs, full migration convergence or all peers' readiness**.
Local tests run logical peers on macOS; actual remote host execution is still a rollout
gate. A stale retained proof is not live connectivity. `--activate` requires the receipt
to be present and valid again, not just a boolean from an old wizard session.

## Upgrade activation (separate, coordinated)

Pause/coordinate all participating writers, drain unsigned outboxes and inbound
history on every peer, verify exact legacy receipts and preserve backups as described
in SIGNED-SYNC.md. Then, and only after operator approval:

```sh
PYTHON security_wizard.py --database DB --exchange EXCHANGE --security-dir KEYS --state STATE resume --activate --confirm-both-peers --confirm-legacy-boundary
PYTHON sync_worker.py DB EXCHANGE --once --security-dir KEYS
```

The confirmations are explicit operator assertions, not automatic remote detection.
Unsigned unpublished outboxes and unresolved local diagnostics block activation.
The production DB latch is set only at this step; identity/pairing/probe steps leave
legacy sync working. Old `changes/` history and receipts remain untouched and are
not accepted into strict mode. New strict traffic uses `signed-changes/`.
No service is automatically enabled. Failure leaves policy unchanged unless the
explicit activation DB transaction has already committed; in that crash window,
resume with the same keys and recover the recorded runtime configuration, not a new
identity or unsigned worker. Rollback after activation needs coordinated recovery.

Private state/backup paths and full trust/key material are not dashboard status.
Public status is a sanitized projection: display name, slot, policy, pairing state,
step/next action, prerequisite readiness and authenticated probe state. POSIX private
files use 600/700; Windows NTFS ACL provisioning must be checked by the operator.
