<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/agentmesh-logo-dark.svg">
  <img src="assets/agentmesh-logo.svg" alt="AgentMesh" width="384" height="80">
</picture>

# macOS and Linux setup

This installs the AgentMesh Python/SQLite memory-sync application, not Hermes or every agent application. Install Python 3.10+ with SQLite FTS5 and JSON support and Syncthing separately. Put `python3` and `syncthing` on PATH. Pair devices, accept the exchange folder, and wait for the bootstrap ZIP and manifest to finish syncing. No wrapper runs brew/apt, changes root configuration, or registers services.

Run from the supplied setup directory:

```sh
# macOS
sh setup_macos.sh "/absolute/path/to/exchange"
# Linux
sh setup_linux.sh "/absolute/path/to/exchange"
```

The exchange argument defaults to the directory containing the wrapper. An optional second argument selects the local installation directory. Defaults are macOS `$HOME/Library/Application Support/AgentMesh` and Linux `${XDG_DATA_HOME:-$HOME/.local/share}/AgentMesh`. Keep local application/database storage outside the Syncthing exchange folder. Only exchange artifacts should sync, never the live SQLite database.

Installation starts a foreground worker: the shell remains occupied until Ctrl-C. Running the macOS wrapper does **not** enable a background service. Stop this worker before enabling persistent operation to avoid duplicate workers.

## Manual restart after installation

Use the installation paths reported by bootstrap (`data/runtime.json`). For default locations:

```sh
# macOS
LOCAL="$HOME/Library/Application Support/AgentMesh"
NODE=mac
# Linux instead:
# LOCAL="${XDG_DATA_HOME:-$HOME/.local/share}/AgentMesh"
# NODE=linux
EXCHANGE="/absolute/path/to/exchange"
PYTHON="$(command -v python3)"
"$PYTHON" "$LOCAL/app/sync_worker.py" "$LOCAL/data/$NODE.db" "$EXCHANGE" --interval 60
# Append --once for one cycle only.
```

## Optional macOS user LaunchAgent

Use absolute paths, including an interpreter available at login. Run the generator from the setup source directory:

```sh
LOCAL="$HOME/Library/Application Support/AgentMesh"
EXCHANGE="/absolute/path/to/exchange"
PYTHON="$(command -v python3)"
PLIST="$HOME/Library/LaunchAgents/org.agentmesh.sync.plist"
"$PYTHON" platform_service.py generate --platform macos --python "$PYTHON" \
  --app-dir "$LOCAL/app" --database "$LOCAL/data/mac.db" --exchange "$EXCHANGE" \
  --output "$PLIST" --interval 60
plutil -lint "$PLIST"
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl print "gui/$(id -u)/org.agentmesh.sync"
```

The plist runs at login/loading and restarts the worker. Logs are in the private local `data` directory (`agentmesh.stdout.log`, `agentmesh.stderr.log`), not in the exchange. To stop/unregister: `launchctl bootout "gui/$(id -u)" "$PLIST"`.

Optional read-only PostgreSQL mirroring: supply `OMP_MEMORY_DSN` securely in the environment **when generating**, and append `--postgres` to the generator command. There is no default host/user. This embeds the DSN in the generated mode-0600 plist, so keep it local, never sync/commit/share it. The worker also needs its PostgreSQL driver installed separately. The generator never prints the DSN or accepts it as a command-line argument. Linux service generation does not configure PostgreSQL mirroring.

## Optional Linux systemd user service

```sh
LOCAL="${XDG_DATA_HOME:-$HOME/.local/share}/AgentMesh"
EXCHANGE="/absolute/path/to/exchange"
PYTHON="$(command -v python3)"
UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/agentmesh.service"
"$PYTHON" platform_service.py generate --platform linux --python "$PYTHON" \
  --app-dir "$LOCAL/app" --database "$LOCAL/data/linux.db" --exchange "$EXCHANGE" \
  --output "$UNIT" --interval 60
systemd-analyze --user verify "$UNIT"
systemctl --user daemon-reload
systemctl --user enable --now agentmesh.service
systemctl --user status agentmesh.service
journalctl --user -u agentmesh.service
```

This is rootless user-session configuration, not a systemwide unit. A working systemd user manager/session is required; services may stop on logout. No lingering is assumed or enabled. On systems without a user manager, use foreground operation or a locally approved supervisor. Stop with `systemctl --user disable --now agentmesh.service`.

The generator only writes configuration; it never invokes a shell, registers services, or contacts a network. It rejects relative paths and control characters, quotes spaces/backslashes/quotes, escapes systemd percent specifiers, and disables environment expansion in ExecStart. Output creation is exclusive (no overwrite) and mode 0600; remove an old configuration explicitly after stopping its service if regenerating.

## Identity and validation limits

Currently only one logical node per `mac`, `windows`, and `linux` is supported in a sync group. Do not clone local state or run the same node identity on multiple machines. Windows setup remains documented separately in README-WINDOWS.md.

Tests exercise argument fidelity, plist parsing, validation, private output, and both wrappers on macOS. These tests are **not** Linux-host systemd validation. Verify the generated Linux unit and worker on your actual Linux host before relying on persistent operation.

## Add peers to a new empty group

Use current AgentMesh code on each device. This flow supports **unsigned groups
created by `setup-new` in this version**. It initializes the same empty baseline
and replays packets; it does not export memory rows or start a worker. Keep the
original source as the group's sole invitation issuer.

On the joining device, choose an unused logical slot (`mac`, `windows`, or
`linux`; it need not match the operating system) and a new local root:

```sh
python3 agentmesh.py prepare-join --local-dir /absolute/local/AgentMesh-B \
  --exchange /absolute/exchange --node windows \
  --output /absolute/private-transfer/b-request.json
# Type REQUEST.
```

Transfer the request to the original source through an operator-trusted channel.
On that source:

```sh
python3 agentmesh.py invite-peer --runtime /absolute/AgentMesh-A/data/runtime.json \
  --request /absolute/private-transfer/b-request.json \
  --output /absolute/private-transfer/b-invitation.json --expires-in 86400
# Type INVITE. The slot is now permanently reserved on A.
```

Transfer the invitation back to the same prepared device:

```sh
python3 agentmesh.py setup-join --local-dir /absolute/local/AgentMesh-B \
  --exchange /absolute/exchange \
  --invitation /absolute/private-transfer/b-invitation.json
# Type JOIN.
```

For automation, use `--yes --confirm REQUEST`, `--yes --confirm INVITE`, or
`--yes --confirm JOIN`. `--yes` alone is refused. All paths must be absolute,
without symlinks; transfer files must be outside the installation and exchange.
Outputs never overwrite existing files. Directories are private (0700), files
are owner-only (0600), with native private ACL provisioning/validation on Windows.
Keep transferred files owner-only too (`chmod 600` on POSIX).

A successful join reports `joined` and creates `data/runtime.json` and
`data/memory.db`, with ingestion and summarization disabled. Start/configure the
normal worker separately. Repeat the request/invitation/join flow for the third
slot; **B cannot issue C's invitation**. A checks its own slot, all persisted
reservations, and directories/packet senders observed in the exchange. A silent
invited peer is covered by its reservation. Do not mix this flow with manual
same-group initialization, clone installations, or run another issuer: a silent
unregistered device cannot be detected from exchange packets.

### Invitation and replay contract

- `setup-new` records `_sync_join_origin` in its private SQLite database only
  after verifying an empty baseline. Invitations require that origin record and
  no `baseline:` revisions in history, even if original rows were later deleted.
  Older installations without this provenance fail closed; there is no automatic
  provenance adoption. Groups with nonempty baselines must use their original
  bootstrap. Joined peers do not receive the origin record.
- The request (`agentmesh-join-request-v1`) contains a request UUID, slot, and
  SHA-256 commitment to a random 256-bit recipient secret. The secret stays in
  the joining device's private `data/join-state.json`; never transfer that file.
- The invitation (`agentmesh-join-invitation-v1`) contains its UUID, group UUID,
  reserved slot, issuer slot, origin UUID, `baseline: "empty"`, `policy: "legacy"`,
  request UUID, recipient commitment, and integer Unix `issued_at`/`expires_at`.
  **Neither transfer file contains memory rows or secrets.** These are unsigned
  administrative files: authenticate the source and delivery channel yourself.
  An origin UUID is an assertion from that trusted issuer, not a cryptographic
  signature. No network service or shared online redemption server is used.
- The source commits its reservation before publishing the invitation, under a
  SQLite write transaction. Concurrent invitations cannot allocate the same
  slot. Reservations never expire or recycle, even after file loss, expiration,
  or failed output publication. There is no automatic reissue/release command;
  retain the original invitation, source DB, and prepared recipient state. Loss
  may require maintainer-assisted recovery or a new group. Do not delete ledger
  records to make a slot available.
- Validity defaults to 24 hours (`--expires-in`, 60 seconds through 7 days).
  Consumption checks the recipient's clock and commits local consumed state
  before creating the database. Reuse is refused, and another independently
  prepared device lacks the matching secret. Copying/restoring the private
  prepared state or the origin DB defeats these guarantees, just as cloning a
  live node would. The offline source does not learn whether redemption finished.
- Signed or security-configured sources are refused. Join also refuses signed
  invitations and an exchange containing `signed-changes`. No signing identity
  or trust is established. Keep the group unsigned throughout outstanding joins;
  offline invitations cannot detect an unpropagated policy change on the source.
  Signed onboarding remains the separate security pairing workflow.
- Joining only reads the exchange. Uncaptured source writes arrive after the
  source's next normal capture/publish. Pending predecessors leave a guarded
  partial installation and exit 2; conflict/invalid reports block completion and
  exit 1. After missing packets arrive, retry with
  `setup-join --local-dir ... --exchange ... --resume` (type `RESUME`, or use
  `--yes --confirm RESUME`). This resumes the consumed invitation, including
  after expiry; it does not allocate another slot. Workers remain blocked by
  `.setup-pending.json` until replay finishes. Retain partial state after errors
  or interruption; do not bypass the guard.

**Never prune exchange packets.** Replay time and storage grow with history.
A missing predecessor may leave successors pending, but deleting the newest
packet (or an entire independent chain) can silently lose data: zero diagnostics
is not proof of complete history. Wait for Syncthing to finish transferring the
full history before joining. The invitation is not a history inventory or a
replacement for backups.
