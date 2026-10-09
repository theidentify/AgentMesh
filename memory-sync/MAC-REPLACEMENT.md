# Mac operator replacement (local development trial)

This is a **Mac-first, operator-run trial**, not a Developer ID signed/notarized
release, production deployment, Windows service installer, baseline join, key
recovery or OTA installer. Building/testing it does not replace your running
worker. The existing source directory remains untouched for rollback.

The native build produces `agentmesh` and adjacent `Replace-AgentMesh.command`.
End users do not need Python/Git. macOS may require the user's explicit local
approval to open the development binary. Do not disable system security or
publish this artifact as a trusted signed download.

## Before running

- Keep SQLite, WAL, identity, wizard/workflow/runtime, installed code and backups
  **outside** Syncthing exchange. Only immutable exchange packets use transport.
- Keep membership, keys, signing policy, service configuration and deployment
  paths stable during the trial. Pause other manually started sync workers.
  Independent normal DB writers can continue; a SQLite backup is a snapshot,
  not a two-way merge or a transaction spanning filesystem/service changes.
- Use the current user's existing LaunchAgent, not a root/system service.
  Select the exact existing plist and its complete `ProgramArguments` array.
  Unexpected live commands/owners, partial setup, OTA-owned installation,
  PostgreSQL service options and unsupported service shapes fail closed.
- Read-only planning uses an informational SQLite snapshot where appropriate.
  It never authorizes signing or replacement. Current DB scope is checked again
  after operator confirmation and before draining/changing state.

## Private manifest

Create a private (`chmod 600`) JSON file **outside exchange**. Replace the
placeholders with discovered absolute paths; do not commit this file. Do not add
private key contents, trust-store contents, transcript data or DSNs to it.
The legacy Python executable can retain its explicitly selected venv symlink;
mutable data/config paths cannot use symlink/reparse traversal.

```json
{
  "plist": "/absolute/Library/LaunchAgents/org.example.agentmesh.plist",
  "expected_args": [
    "/absolute/old-venv/bin/python",
    "/absolute/old-source/sync_worker.py",
    "/absolute/old-source/data/mac.db",
    "/absolute/exchange",
    "--interval", "60.0"
  ],
  "app_root": "/absolute/old-source",
  "runtime": "/absolute/old-source/data/runtime.json",
  "database": "/absolute/old-source/data/mac.db",
  "exchange": "/absolute/exchange",
  "node": "mac",
  "security_dir": "/absolute/existing-private-identity",
  "security_state": "/absolute/existing-private-state/wizard.json",
  "workflow_config": "/absolute/old-source/data/workflow.json",
  "install_root": "/absolute/private-parent/agentmesh-versions",
  "backup_root": "/absolute/private-parent/agentmesh-replacement-backups"
}
```

`app_root` and the runtime's parent must already exist. The old worker script
must be under `app_root`. Install/backup roots may be new direct children of
existing user-owned parents; they must not overlap each other, exchange,
identity or active data/config files. Existing roots must already be private.
The actual workflow file must exist; all its settings remain unchanged.
Strict services must already have the exact matching `--security-dir` in their
old command. A pending identity on a legacy DB does **not** authorize that flag.

## Operator flow

```sh
/absolute/dist/agentmesh mac-replace \
  --manifest /absolute/private/replacement.json --dry-run

/absolute/dist/Replace-AgentMesh.command /absolute/private/replacement.json
```

Alternatively run `agentmesh mac-replace --manifest ...` directly from the
frozen binary. Source invocation also requires `--binary /absolute/dist/agentmesh`.
The default wait bound is 300 seconds (`--timeout` can change it).

1. Print a read-only, nonsecret plan. Verify label, runtime, policy and binary
   checksum. Decline/EOF performs no install/service operation.
2. Require **REPLACE**. Revalidate exact manifest, plist, workflow/runtime bytes,
   path identities, selected code and current DB scope.
3. Drain the discovered launchd label and wait for the actual old process to
   exit. The old worker may not participate in any new kernel lock: absence of
   `worker.lock` is **never** proof it stopped. No new cycle starts before drain.
4. Create an owner-only recovery directory. Use SQLite's backup API, including
   committed WAL data; close handles and verify integrity and foreign keys.
   Back up the plist, workflow, existing runtime and old-code descriptor.
   No logs, private identities/trust stores or raw deployment tree are copied.
   Exact private service/config snapshots can contain environment credentials;
   retain them privately and never share them as release assets.
5. Install the binary under its immutable SHA-256 version directory. Never copy
   it over the legacy source. If runtime is missing, additionally require
   **BIND**: this creates only a private runtime binding. Existing runtime is
   never overwritten. Declining binding restores the old service.
6. Run one bounded **real bundled cycle**, with the selected workflow and the
   preserved DB policy, and no forced restart summary or PostgreSQL option.
7. Replace only plist arguments, preserving the label, environment, logging
   paths, RunAtLoad/KeepAlive and other plist properties. Bootstrap it; require a
   new owned instance and completed healthy cycle, not just launchd `running`.
   PyInstaller's supervising bootloader and child are checked as one instance.
8. Return `status=replaced` and the recovery directory. An ordinary failure
   attempts code/service rollback. Exit 1 means failure even if that rollback
   succeeded; `recovery_required` means stop and inspect the retained evidence.

## Rollback

```sh
/absolute/dist/agentmesh mac-rollback --backup /absolute/recovery-directory
```

Require **ROLLBACK**. Verify the recovery descriptor, unchanged old
entrypoint/interpreter, expected current plist/live command and current DB
node/group/policy/key binding. Stop only the selected service, restore its old
plist and bootstrap it. Remove a newly adopted runtime only if its exact owned
bytes still match; preserve preexisting runtime and the untouched workflow.
Changed code/config/scope or uncertain ownership requires manual recovery,
not an arbitrary overwrite/restart. Power loss or process death midway also
requires review of durable recovery state; this is not a crash-atomic installer.

**SQLite is NEVER automatically restored**, including after successful new
cycles or concurrent committed writes. `database.sqlite` is a verified recovery
snapshot for separately approved recovery, not an undo button. Keys, signing
policy, valid newer data, independent ingestion/digest cron and source files
are not reverted. Source-level API tests inject launchd only; testing that
boundary does not prove the user's actual launchd/Gatekeeper host trial.

## Managed lifecycle and adoption without service replacement

`adopt-install` requires explicit `--runtime`, `--app-root`, `--database`,
`--exchange`, `--node`, `--security-dir`, `--security-state` and
`--workflow-config`, followed by **BIND**. No schema initialization, identity
creation, activation, group join or background start occurs.

```sh
agentmesh worker-status --runtime /absolute/data/runtime.json
agentmesh worker-run --runtime /absolute/data/runtime.json --once --legacy-drained
agentmesh worker-start --runtime /absolute/data/runtime.json --legacy-drained
agentmesh worker-stop --runtime /absolute/data/runtime.json
```

For a strict DB omit `--legacy-drained`; valid persistent node/group/sender/key
binding and non-revoked self trust are required **before any ingestion/summary/
status write**. A legacy DB requires explicit `--legacy-drained` acknowledgement
that the old worker is stopped and unsigned policy is intended, even if an
identity/wizard state exists. Never use it to bypass draining the old launchd
worker. Membership/activation still uses the existing explicit wizard gates.

`worker-run` holds a lifetime kernel lock separate from each cycle's
`worker.lock`; current runtime bytes and authoritative DB/key scope are
revalidated inside the cycle lock before mutations. OTA active-version fences
remain effective. Restart never forces summarization. `worker-start` succeeds
only after an actual healthy cycle. Status does not create locks, repair state
or open setup. Stop uses private nonce-bound metadata and a cooperative request;
it waits for acknowledgement and lifetime-lock drain, never kills a PID read
from a file. Stale/corrupt/foreign metadata refuses safely. A managed worker
exits nonzero on a failed cycle rather than silently claiming healthy status.
