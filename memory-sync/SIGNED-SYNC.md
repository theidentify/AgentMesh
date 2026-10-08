# Signed change packets (opt-in)

This source implements Ed25519 authentication and local pinned peer trust. It does
**not** activate signing, create production keys, publish a bootstrap package, pair
Windows, or change running services. Existing unsigned databases and workers keep
using `omp-memory-changes-v1` without needing cryptography.

## Identity and authorization

A persistent random `sender` UUID identifies a machine; `node` still means one of
`mac`, `windows`, `linux`, the existing allocation slots. A key is explicitly pinned
to **group UUID + sender UUID + allocation slot**. A public key inside a proposal or
packet is never authority. The fingerprint is the full lowercase SHA-256 of the raw
32-byte Ed25519 public key. Compare it over an independent authenticated channel.
Approvals and revocations are local files, not synchronized trust announcements.

This does NOT implement an arbitrary-node registry or allocation migration. At most
one active sender per legacy slot per group is allowed in each trust store. Two Macs
cannot simply choose different display names and safely share the same range.
Authorized peers can modify existing shared records; this is group-level writer
trust, not per-table or per-record authorization. Signing does not encrypt content.
Status JSON is still unsigned telemetry, not cryptographic proof of application.

## Dependencies and local storage

Use Python 3.10+ and install the separately pinned dependency into the interpreter
that actually runs the worker. Legacy bootstrap remains stdlib-only by default.
No dependency is silently installed, no crypto is vendored, and strict mode fails
closed if cryptography cannot load. From the extracted `memory-sync` sources:

```sh
python3 -m venv /local/private/agentmesh-venv
/local/private/agentmesh-venv/bin/python -m pip install -r requirements-security.txt
```

Windows PowerShell equivalent (Python must already be installed):

```powershell
py -3 -m venv "$env:LOCALAPPDATA\AgentMesh\venv"
& "$env:LOCALAPPDATA\AgentMesh\venv\Scripts\python.exe" -m pip install -r requirements-security.txt
```

If pip is unavailable, use the operator's approved package tooling (for example
`uv pip install --python <worker-python> -r requirements-security.txt`); do not fall
back to checksums. `cryptography==46.0.7` is the tested pin, not a claim that it is
the newest release. Review and deliberately test upgrades before changing the pin.
The private identity is unencrypted raw key material: disk/account protection and
secure backups are the operator's responsibility. Keep the entire security directory
outside Syncthing, Git, source trees, snapshots, public exports, and screenshots.

POSIX creation uses directory 700 and files 600; private/trust file reads enforce
owner identity and no group/other permissions. Symlink paths are refused. Windows
mode bits do not enforce NTFS ACLs: provision a user-private directory, inspect with
`icacls <directory>`, and restrict inherited access using the site's account policy.
No Windows ACL verification is claimed by the local tests. Do not copy a private
key to another machine; export only its public identity.

## Explicit pairing CLI

Replace the placeholders with already approved group/slot values; never invent a
new group for a database already initialized from a bootstrap manifest. `PYTHON`
below means the actual worker interpreter.

```sh
PYTHON signed_packets.py --security-dir /local/private/keys init --group GROUP_UUID --node mac
PYTHON signed_packets.py --security-dir /local/private/keys fingerprint
PYTHON signed_packets.py --security-dir /local/private/keys export-public --output /local/public/mac-key.json
# Transfer ONLY mac-key.json. Read the remote full fingerprint out of band.
PYTHON signed_packets.py --security-dir /local/private/keys trust /local/public/windows-key.json --fingerprint FULL_SHA256 --group GROUP_UUID --node windows --sender REMOTE_UUID
PYTHON signed_packets.py --security-dir /local/private/keys revoke --fingerprint FULL_SHA256
```

The public export is no-clobber. Initialization refuses an existing directory,
including partial state. Trust cannot resurrect a revoked fingerprint. Serialize
operator trust edits; this file store is not a concurrent administrative database.
A proposal is untrusted until each receiving operator approves it. A peer does not
become trusted because Syncthing reports 100% delivery.

## Coordinated migration boundary (no silent legacy exception)

1. Verify new sources and the signing dependency on **every participating host**.
   Preserve original databases, exchange history, node/group configuration and keys.
   Pause writers/workers only when a coordinated rollout is actually authorized.
2. While still in legacy mode, capture and publish every unsigned outbox packet and
   require remote database application receipts on all peers. Resolve all pending,
   invalid and conflict diagnostics. A published flag is not remote acknowledgment.
   Record the exact last legacy packet IDs and per-peer `_sync_receipts`; compare
   application state and causality at the boundary. Offline peers must catch up first.
3. Use SQLite's backup API for recovery copies, not copying a live DB/WAL. Pair public
   keys explicitly, test signed roundtrip on isolated copies, and verify remote
   application/recall rather than transport delivery. Never rewrite or re-sign an
   already committed unsigned UUID: that changes its immutable meaning.
4. Strict mode uses a new `signed-changes/<slot>/<packet-uuid>.json` namespace within
   the accepted exchange. Keep `changes/` as the frozen legacy archive, unchanged.
   Strict mode does **not** scan or accept this archive; there is no unsigned
   allowlist, time cutoff, TTL, or checksum downgrade. Old workers ignore the new
   namespace, so mixed running protocols will diverge: coordinated activation is
   mandatory. Pending old inbound packets left in `changes/` will not catch up after
   activation; finish step 2 before crossing this boundary.
5. Only after operator approval on all peers, run:

   ```sh
   PYTHON memory_sync.py once /local/memory.db /local/exchange --security-dir /local/private/keys
   # or the normal worker with the same explicit security directory:
   PYTHON sync_worker.py /local/memory.db /local/exchange --once --security-dir /local/private/keys
   ```

   A first strict operation binds `_sync_security` to group/slot/persistent sender.
   It refuses **any unpublished unsigned outbox**, even if the text could be signed.
   Once bound, these sync APIs/worker reject omission of the security directory;
   restart with the same identity/trust. This latch is not a barrier against old
   source code or a local administrator. Do not restart an old worker on a bound DB.
6. Verify an exact new packet UUID's committed receipt, resulting rows/provenance,
   and remote recall. Then enable the chosen worker scheduling separately. Do not
   regard a status file or process existence as a verified roundtrip. Preserve old
   history and receipts; signature checks do not replace replay/echo ledgers.

Failure before activation leaves legacy mode available. After signed state exists,
rollback is a coordinated recovery operation from the pre-boundary backup plus
reconciliation of subsequent writes, NOT removing the latch or dropping history.

## Rotation and revocation

Generate a replacement into a **new** private directory, with `init --sender` set
to the existing sender UUID and the same group/slot. Verify the new fingerprint
out of band on every receiver; approve it explicitly, then revoke the old key at
the agreed boundary. Do not change the production identity merely to test rotation.
Drain signed outboxes before revoking their signing keys; old unpublished packets
cannot be silently rewritten. Verification reloads pinned trust and checks signatures
before the replay ledger, so revoked retained packets are rejected, including those
already applied. Historical receipts/rows remain intact; retained revoked files
can show invalid diagnostics until an operator reviews the archive. Do not delete
history to hide them. Replacing a lost key is explicit recovery, not auto-generation.

## Wire contract

Envelope has exactly `format`, `encoding`, `group`, `node`, `sender`, `uuid`, `body`,
`checksum`, `key_id`, `signature`. Format is `agentmesh-signed-changes-v2`, encoding
is `agentmesh-typed-v1`; unknown versions/fields are rejected. No embedded public key.
The checksum is SHA-256 of typed body bytes. The signature is raw Ed25519 (64 bytes,
lowercase hex) over domain bytes `AgentMesh/change-packet/Ed25519/v2` followed by a
NUL byte and the typed complete envelope **excluding only signature**. Signed
receipt checksums hash that same domain-separated authenticated envelope, binding
headers as well as content. Schema/range/causality/FK validation still runs before
any database apply; receipt and application changes commit in the same transaction.

This is deliberately **not RFC 8785 / JCS**: Python's sorted JSON is not JCS. JSON
is the transport representation, independent of whitespace/key presentation.
Deterministic typed binary encoding is defined recursively as follows (all counts
and integer text use ASCII decimal, no padding; all values are length-delimited or
fixed-length):

| Value | Encoding |
| --- | --- |
| null / true / false | `n` / `t` / `f` |
| integer | `i` + minimal signed decimal + `;`, signed 64-bit only |
| floating number | `d` + 8-byte IEEE-754 binary64, big endian, finite only |
| string | `s` + UTF-8 byte count + `:` + strict UTF-8 bytes |
| list | `l` + element count + `:` + concatenated encoded elements |
| object | `o` + pair count + `:` + encoded key/value pairs, sorted by key UTF-8 bytes |

Integer and float are distinct. JSON integer tokens decode as signed integers;
tokens with a fraction/exponent decode as binary64. Floating negative zero is
preserved; integer `-0` is integer zero. Unicode is not normalized; surrogate code
points, duplicate decoded JSON keys, nonfinite/overflowing float values, unsupported
types, >64 nesting depth, >100,000 recursively counted values, and packets/files or
encoded payloads >8 MiB are rejected. Filename must match the canonical packet UUID
and the folder must match the authorized allocation slot. Signature verification
happens before row reconciliation and before trusting replay receipts.

The transport is not trusted for authenticity. Symlink components, nonregular
files and traversal-like identities are rejected; bounded reads prevent FIFO/large
file reads. This does not defend against a local administrator changing directories
concurrently, stealing keys, corrupting trust/DB files or replacing this source.
Unbounded numbers of transport files remain a possible availability attack.

## Verification

Run `python -m pytest -q` from `memory-sync` using the signing interpreter. Tests use
fresh isolated keys and disposable databases: field/body tampering, unknown/revoked
keys, rotation, unsigned rejection, downgrade refusal, scope/folder/file attacks,
Unicode/numbers/duplicates/nonfinite/size limits, transaction rollback, replay/no
 echo, two-way eight-table exchange, provenance, and missing-crypto behavior. Local
logical `windows` fixtures do not establish Windows-host execution or ACL safety.

Primary references: cryptography's Ed25519 documentation
(https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/) and RFC 8785
(https://www.rfc-editor.org/rfc/rfc8785). Ed25519 signing/verification is provided
by cryptography, not a handwritten cryptographic primitive.
