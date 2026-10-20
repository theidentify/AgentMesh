# Encrypted Profile Log, Phase 3 (Memory Broker) Implementation Plan

> **For agentic workers:** execute in order. The spec is
> `docs/superpowers/specs/2026-10-18-encrypted-profile-log-design.md` ("Memory broker", "Sensitivity labels",
> "Events", Phase 3); read it fully first, then `profile-log/README.md` and `profile-log/FORMAT.md`. The spec
> wins on conflict, except where this plan records an owner decision. Stop and report instead of guessing.

**Goal:** a local memory broker in `profile-log/` that is the only process holding decrypted scope keys.
Agents connect over a local socket, authenticate with their certified Ed25519 sub-key, and can recall and
write only in the scopes they were granted. The broker keeps one index per agent, logs every query, runs
secret/PII detection at write time, and works fully offline against a local replica with an encrypted outbox.
Out of scope: auto-start, rewiring `recall_memory.py`/ingest, enrollment, keyring, biometrics, migration.

## Owner decisions (2026-10-19)

- **Detectors: built-in regex rules, no new dependency.** High-confidence secret rules in the style of
  gitleaks plus PII rules. A secret is redacted to `[REDACTED:<kind>]` and the event label is raised to
  `secret`. PII is not redacted and raises the label to `internal`. False-positive budget, measured on a
  synthetic benign corpus: secret ≤ 1 %, PII ≤ 5 %. A `Detector` interface lets detect-secrets or Presidio
  plug in later.
- **IPC: AF_UNIX on macOS/Linux, a named pipe on Windows**, the same length-prefixed JSON framing on both.
  Agents authenticate by challenge-response with their agent sub-key. The broker authenticates itself with the
  device key, which the agent has pinned, so the agent refuses an impostor broker.
- **Index: new, in `profile-log/`, ranked like `memory-sync/recall_memory.py`.** This keeps recall
  behaviour close to today's when the live SQLite data is migrated in Phase 5. Port the query analysis and
  scoring (Thai splitting, stop words, intents, kind weights). Port `profile-memory/agentmesh_memory/vectors.py`
  as-is for a later semantic mode; the semantic mode itself is not wired up in this phase. Import nothing from
  `memory-sync/` or `profile-memory/`.
- **Concurrent revisions: keep every head.** Recall shows the newest head (by `created_at`, then ULID) with
  `conflict: true` and the other heads listed. No automatic merge. A resolve command is deferred.
- **Auto-start and caller rewiring stay out of Phase 3.** Nothing under `memory-sync/`, installers, services,
  workflows or `platform_service.py` changes.
- **Keys at rest: a `KeyStore` interface, implemented in Phase 3 as one owner-only (0600) file.** README
  documents this as a Phase 3 limitation; Phase 4 moves the store into the OS keychain via `os_unlock`.
  **Audit log: append-only JSONL with an HMAC digest of the query text, never the text itself.**
- **`secret`-labelled results are withheld** unless the agent's grant explicitly allows `secret`. The
  biometric prompt the spec asks for here, and for cross-profile and first access, belongs to Phase 4. Leave
  `TODO(phase4-presence)` at those points and nothing more.

## Threat model (what Phase 3 does and does not guarantee)

Guaranteed: an agent that talks only to the broker gets data only from scopes it was granted. No plaintext
memory or index ever reaches disk, so a second agent running as the same OS user finds no shared SQLite file or
plaintext index to read. Agents hold only their own signing key and certificate, never a data key. Every
request, including denied and failed ones, is audited.

Not guaranteed: protection against **malicious code running as the same OS user**. Such code can read the
0600 key-store file or another agent's key file directly. Defending against it needs the broker under a
separate OS account (installer work, deferred) or OS keychain ACLs (Phase 4). README must say this plainly.

## Design to implement

### Layout (all new files under `profile-log/`)

```
agentmesh_log/
  events.py          + prepare_event / attach_signature (seal_event refactored onto them, wire format unchanged)
  broker/
    __init__.py
    framing.py       4-byte big-endian length + canonical JSON (encoding.wire/parse), max 1 MiB
    transport.py     UnixServer/UnixClient (POSIX), PipeServer/PipeClient (Windows); peer checks
    handshake.py     mutual challenge-response (device key ↔ agent key)
    keystore.py      KeyStore protocol + FileKeyStore (0600, atomic replace)
    grants.py        Grant model, scope resolution (read covers descendants), revocation
    detect.py        Detector protocol, RegexDetector, redact(), rule table
    query.py         port of recall_memory.analyze_query/_score (attributed in the docstring)
    vectors.py       port of profile-memory vectors.py (unused by default; tested)
    index.py         AgentIndex: in-memory SQLite FTS5 per agent; encrypted snapshot load/save
    heads.py         revision graph → heads, conflict groups
    replica.py       local FilesystemStorage replica, outbox, sync_once(remote)
    audit.py         append-only JSONL, HMAC query digest, fail-closed
    service.py       Broker: request dispatch independent of transport; threaded serve loop
    client.py        BrokerClient for agents (loads agent key file, pins device key)
  cli.py             `python -m agentmesh_log.cli ...` (see CLI)
tests/
  broker/...         one test module per component + test_isolation.py + test_offline.py + test_cli.py
  fixtures/detector_corpus/benign.jsonl
```

### Broker home

`BrokerHome(root)`: `root/keystore.json` (0600), `root/agents/<agent_id>.key` (0600, handed to the agent and
not read again by the broker), `root/replica/` (FilesystemStorage), `root/index/<agent_id>.snap` (encrypted),
`root/audit.jsonl`, `root/broker.sock` (POSIX). Root and subdirectories are created 0700. On POSIX, refuse to
start if root is not owned by the current uid or is group/world accessible. Refuse symlinks (reuse
`storage.filesystem._check`).

### Key store (`keystore.py`)

`KeyStore` protocol: `load() -> BrokerState`, `save(state)`. `BrokerState` holds the account public record and
the account root signing key (needed to certify the first device; Phase 4 moves it off daily devices), this
device's identity (private key), the registry device certificates, every profile's `ScopeTree` (scopes, local
names, labels, every epoch's private key, wrap records), the grant table, `index_master_key` (32 random bytes)
and `audit_key` (32 random bytes). Serialization is `agentmesh-broker-state-v1` canonical JSON. Private keys
are lowercase hex and appear nowhere else.

`FileKeyStore` writes atomically: temp file 0600 in the same directory, fsync, `os.replace`, fsync the
directory where supported. Add the small `from_private`/`private_bytes` helpers needed in `keys.py`,
`identity.py` and `model.py` to round-trip state. Existing public behaviour and FORMAT v1 stay unchanged.

### Agents and grants (`grants.py`)

- `broker grant` creates an `AgentIdentity` on this device. It writes the agent key file
  `{"format":"agentmesh-agent-key-v1","agent_cert":…,"agent_private_key":hex,"device_public_key":hex,"address":…}`
  (0600) and stores a grant: `agent_id`, the pinned `agent_public_key`, `read` (list of scope IDs), `write`
  (list of scope IDs), `default_write` (one scope ID, the narrowest), `default_profile`, `allow_secret`
  (bool) and `allow_cross_profile` (bool). The broker discards the agent private key after writing the file.
- A read grant on a scope covers its descendants, matching the key tree. Granting `work/client-a` covers only
  `client-a`. A write grant covers exactly the listed scopes, never their descendants or parents.
- `broker revoke AGENT` deletes the grant. The broker re-reads grants (by key-store mtime plus a content
  digest) **on every request**, not only at handshake, so revocation takes effect on the next request of an
  open session.
- Names: CLI and requests address scopes as `profile` or `profile/project` (local names, resolved by the
  broker). Unknown and ungranted names give the same `denied` response, so they do not reveal which scopes
  exist.

### Transport and framing (`framing.py`, `transport.py`)

- Frame: 4-byte unsigned big-endian length, then `encoding.wire(obj)`. Reject length 0 or > 1 MiB before
  reading the body, and close the connection on any framing or parse error. Per-connection read timeout 30 s.
- POSIX: `socket.AF_UNIX` / `SOCK_STREAM` at `root/broker.sock`. Unlink a stale socket only if it is a socket
  owned by us. Chmod the socket to 0600 after bind. Check the peer uid via `SO_PEERCRED` (Linux) or
  `getpeereid` through `ctypes` (macOS) and refuse other uids. The peer-uid lookup is an injectable function so
  tests can simulate a foreign uid.
- Windows: `multiprocessing.connection.Listener(address=r'\\.\pipe\agentmesh-broker-<device_id[:16]>',
  family='AF_PIPE')` and `Client`, using **only `send_bytes`/`recv_bytes`** (never `send`/`recv`, which
  pickle). Do not use `authkey`. Authentication is the handshake below. The default pipe DACL denies other
  users write access; document that local administrators can still connect.
- macOS caps `sun_path` at 104 bytes. Tests create short roots with `tempfile.mkdtemp(prefix='am', dir=…)`
  under `/tmp` on POSIX. If the sandbox denies binding, the test fails explicitly; it does not skip.

### Handshake (`handshake.py`)

1. C→B `{"format":"agentmesh-broker-hello-v1","client_nonce":b64url(32)}`
2. B→C `{"format":"agentmesh-broker-challenge-v1","device_id","server_nonce":b64url(32),"signature"}`.
   The device key signs domain `AgentMesh/broker-challenge/Ed25519/v1\x00` + typed({device_id, client_nonce,
   server_nonce}). The client verifies it with the pinned `device_public_key` from its key file. On failure
   it closes the connection and raises `BrokerIdentityError`.
3. C→B `{"format":"agentmesh-broker-auth-v1","agent_cert","signature"}`. The agent key signs domain
   `AgentMesh/broker-auth/Ed25519/v1\x00` + typed({device_id, agent_id, client_nonce, server_nonce}). The
   broker verifies the certificate chain via the registry, checks that `agent_cert.device_id` is this device,
   that a grant exists for `agent_id` and that `agent_public_key` matches the pinned key, then verifies the
   signature against this connection's nonces.
4. B→C `{"format":"agentmesh-broker-ready-v1","agent_id"}`. Any failure returns
   `{"error":"unauthenticated"}`, is audited and closes the connection. A session is bound to one connection;
   nonces are never reused.

### Requests (`service.py`)

Every request is `{"op":…, …}` with an exact field set per op; unknown ops and extra fields are rejected.
Responses have exact field sets. **No op returns key material, wrap records, other agents' IDs or grants.**

| op | fields | result |
| --- | --- | --- |
| `whoami` | — | `agent_id`, readable/writable scope **names**, `default_profile`, `allow_secret` |
| `recall` | `query` (1–2048 chars), `project`, `task`, `limit` (1–100, default 12), `profiles` (list or null), `scopes` (list of scope names or null) | recall shape below |
| `remember` | `body`, `scope` (name or null → `default_write`), `tags`, `task`, `label` (agent label or null) | `{"stage":"sign","unsigned": <envelope without signature>}` |
| `revise` | as `remember` plus `revises` (ULID) | same two-step |
| `sign` | `signature` (for the pending unsigned envelope of this session) | `{"id": ULID, "label": …, "redactions": [kinds]}` |

- **Two-step writes:** the broker resolves the scope, runs the detectors and redaction, resolves the label
  (`resolve_label(scope_default, detected, agent)`), builds the payload and calls `events.prepare_event`,
  which returns the header plus ciphertext. The agent checks that the header carries its own
  `agent_cert`/IDs and the expected scope, then signs `EVENT_DOMAIN + typed(unsigned)` with its own key.
  `attach_signature` produces the final event; the broker verifies it with `verify_event`, then stores and
  indexes it. The broker never holds agent private keys. One pending write per session; any other op discards
  it.
- `revise`: the revised event must exist in the replica, be readable by this agent, and be in the **same
  scope** as the new revision; otherwise `denied`. The broker passes the previous label as `previous_label`.
  Lowering needs user confirmation, which no agent can give in Phase 3, so a lowering revise is refused with
  `label_lowering_requires_confirmation`.
- `recall` scope selection: `scopes=null` searches every scope the agent may read within the selected
  profiles. A list of names (`work/client-a`, `work`) restricts the search to those scopes (a named scope
  includes its descendants). Naming any scope the agent may not read, or one outside the selected profiles,
  returns `denied` for the whole request; it is never silently dropped. `project` stays what it is in
  `recall_memory`: a filter on the body's `project` field, not a security boundary.
- `recall` defaults to `profiles=[default_profile]`. More than one profile requires `allow_cross_profile` and
  is audited as `cross_profile: true` (`TODO(phase4-presence)`). Results come only from this agent's index.
- Errors are fixed codes (`denied`, `invalid_request`, `unauthenticated`, `unavailable`,
  `label_lowering_requires_confirmation`, `internal`). They carry no plaintext, key material or scope names
  the agent cannot read.

### Recall shape (compatible with `recall_memory.recall`)

Top level: `query`, `query_plan` (`query`, `project`, `task_refs`, `intents`, `terms`), `candidate_counts`
(`{"durable": n, "summaries": 0, "events": 0}`), `results`, `evidence` (always `[]` in Phase 3). Each result:
`id` (ULID), `source_type` (`"durable"`), `kind`, `content`, `project`, `task_ref`, `confidence`, `scope`
(name), `profile` (name), `label`, `tags`, `created_at`, `updated_at` (ISO-8601 UTC), `matched_terms`, `score`,
`conflict` (bool), `alternatives` (list of ULIDs). Scoring and selection follow `recall_memory._score` and its
selection rule (drop rows with fewer than two matched terms when the query has two or more terms and no task
refs).

**Memory body convention:** an event body is either a string (treated as `{"kind":"fact","content":…}`) or an
object with `kind` (string), `content` (string) and optional `project`, `task_ref`, `confidence` (0–1) and
`metadata` (object). The broker validates this on write; events from the replica that violate it are indexed
as `kind: "fact"` with `content` set to the canonical JSON of the body.

### Per-agent index (`index.py`, `heads.py`)

- One `AgentIndex` per **agent_id**, never shared, even between agents with identical grants. Each is a
  `sqlite3.connect(':memory:')` with `PRAGMA temp_store=MEMORY`, an FTS5 table (`unicode61`) and a metadata
  table. It contains only events whose scope is in that agent's readable set (descendants included) and, unless
  `allow_secret`, whose label is not `secret`. Every token is quoted in MATCH, as in `recall_memory`. Use
  substring scoring for Thai, as the ported `_score` does.
- Build: open each replica event with the broker's keys and insert it into every index whose agent can read
  it. When an agent's grant digest changes, drop and rebuild that agent's index.
- Snapshot (optional persistence): `Connection.serialize()` sealed with ChaCha20-Poly1305. The key is
  HKDF-SHA256(`index_master_key`, info=`AgentMesh/index-snapshot/v1\x00` + agent_id), the nonce is 12 random
  bytes, and the AAD is typed({agent_id, grant_digest, replica_watermark}). Load with `deserialize` into
  `:memory:`. On any mismatch or failure, rebuild. Plaintext SQLite pages never touch disk. Missing FTS5 is
  an explicit `unavailable` error.
- Heads: within one scope, link each event to its `revises` target if that target is in the same scope;
  otherwise treat it as a root. Group connected components. Heads are events no other event revises. A group
  with more than one head is a conflict: recall returns the newest head (`created_at`, then ULID), with
  `conflict: true` and the other heads in `alternatives`. Superseded revisions are not returned.

### Detection (`detect.py`)

- `Finding(kind, category: "secret"|"pii", start, end)`. `RegexDetector.scan(text) -> list[Finding]`. It
  scans every string in `body` (values, recursively), `tags` and `task`. Object keys longer than 64 characters
  or matching a secret rule cause the write to be rejected (`invalid_request`).
- Secret rules (each a named, anchored, bounded regex; no catastrophic backtracking):
  PEM private key blocks, AWS access key IDs (`AKIA|ASIA` + 16), GitHub tokens (`ghp_|gho_|ghu_|ghs_|ghr_`,
  `github_pat_`), Slack tokens (`xox[abprs]-`), Anthropic (`sk-ant-`), OpenAI-style `sk-` keys with length ≥ 32,
  Google API keys (`AIza` + 35), Stripe live keys, JWTs (three base64url segments, header decodes to JSON with
  `alg`), and `password|passwd|secret|token|api_key` `=`/`:` assignments whose value has Shannon entropy
  ≥ 3.5 bits/char and length ≥ 12.
- PII rules: email address, phone numbers (`+66` and Thai `0X-XXXX-XXXX` / `0XX-XXX-XXXX` forms, E.164),
  Thai national ID (13 digits, valid checksum), payment card numbers (13–19 digits, Luhn-valid, known IIN
  prefixes).
- `redact(text, findings)` replaces each secret span with `[REDACTED:<kind>]`, merging overlaps. PII is kept.
  The response's `redactions` lists kinds only, never matched text. The audit log records kinds and counts.
- **Never commit realistic secrets.** Positive test cases are generated at test runtime from fragments, for
  example `'AKIA' + ''.join(random.choices(...))`, so that secret scanners and GitHub push protection do not
  trigger. The benign corpus is a committed synthetic JSONL file (≥ 500 lines): ordinary prose in English and
  Thai, code, git SHAs, UUIDs, ULIDs, hex digests, URLs, version strings, dates, prices, file paths, base64 of
  plain text and IDs that look like the agentmesh ones. It contains no real data.

### Replica, outbox and sync (`replica.py`)

- The local replica is a `FilesystemStorage` under `root/replica/`. Writes are stored there first, always,
  with no network. The **outbox** is the set of replica event and wrap keys missing from the remote's last
  successful listing; `outbox()` reports its size.
- `sync_once(remote: Storage)`: list `events/` and `keys/` on both sides, then put local-only objects to the
  remote and get remote-only objects. Each fetched event goes through `verify_event` against the registry
  before it enters the replica; invalid ones are skipped and counted, never stored. Index newly stored events.
  Any remote exception leaves the replica and indexes unchanged for that object and is reported as a count.
  It is a method call only: the broker has an optional `--sync-interval` (default off), and no daemon is
  installed.
- Offline means a remote that raises on every call: recall, remember, revise and whoami keep working.

### Audit (`audit.py`)

One JSON line per request and per handshake outcome, appended with flush+fsync **before** the response is
sent. If the append fails, the request fails with `unavailable` (fail closed). Fields: `ts` (ms), `agent_id`
(or null when unauthenticated), `op`, `outcome` (`ok`/`denied`/`invalid`/`unauthenticated`/`error`),
`profiles`, `scopes` (IDs searched or written), `cross_profile`, `query_hmac` (HMAC-SHA256 with `audit_key`
over the UTF-8 query, hex), `query_chars`, `result_count`, `result_ids`, `redactions` (kinds with counts),
`label`. Query text, body text, tags and task never appear in it. The file is 0600 and append-only by
convention; tamper evidence is deferred.

### CLI (`agentmesh_log/cli.py`)

`python -m agentmesh_log.cli <command>`, with JSON output and exit code 0/1 (2 for usage):

- `broker init --home H --profile work --profile personal`: creates the account, device, each profile and
  its `common`.
- `broker scope add --home H work/client-a [--label internal]`.
- `broker grant --home H --name NAME --read work/client-a [--read …] --write work/client-a
  [--default-write …] [--allow-secret] [--allow-cross-profile]`: prints the agent key file path.
- `broker revoke --home H AGENT_ID`, `broker run --home H [--sync-interval S]`.
- `broker sync --home H --filesystem PATH | --s3-bucket B [--s3-prefix P --s3-endpoint URL]`.
- `recall QUERY [--project P] [--task T] [--limit 12] [--profile NAME …] [--scope NAME …] --agent-key FILE`: prints the
  recall JSON shape above, mirroring `recall_memory.py`'s `QUERY --project --task --limit`.
- `remember --agent-key FILE [--scope work/client-a] [--kind fact] [--tag …] [--task …] CONTENT`.

Admin commands edit the key store directly and take an exclusive lock file (`root/.lock`, O_EXCL with PID).
They work while the broker runs because the broker re-reads grants on every request.

## Ground rules

- Work only in `/Users/krisanathep/Workspaces/AgentMesh/.claude/worktrees/bold-hofstadter-af7a0c`. Change only
  `profile-log/**` and `docs/superpowers/plans/2026-10-19-encrypted-profile-log-phase3.REPORT.md`.
  **Do not touch `memory-sync/`, `profile-memory/`, installers, services, workflows or any other docs.**
- **Do not commit, push or create branches.** The reviewer commits.
- No network. Use the prepared interpreter only:
  `~/.cache/agentmesh-logvenv/bin/python -m pytest -q -p no:cacheprovider profile-log/tests` (Python 3.11,
  SQLite 3.53 with FTS5, cryptography 50.0.2, pytest 8.4.2). Do not pip install. No new dependencies.
- Disposable data only. Never read or modify `~/.hermes`, real identities, exchanges or keychains, or the
  user's memory databases. Tests use temporary directories.
- TDD: write each failing test first, confirm it fails for the stated reason, then implement.
- Never print, log or put in exception messages any private key, scope key, plaintext body, query text or
  credential.
- Keep the existing 167 tests passing and FORMAT v1 bytes unchanged (`seal_event` output must still verify
  with the unchanged `verify_event`/`open_event`).
- Match the compact style of `profile-log/agentmesh_log/`. No GATES.md or other working files left behind.
- Sockets and pipes: a test that cannot bind because of the sandbox fails with an explicit message; never
  skip. The reviewer reruns it outside the sandbox.
- Windows code paths must import and run on Windows CI (`windows-2025`); guard POSIX-only calls.

## Tasks

1. **Event two-step + state round-trip.** `prepare_event`/`attach_signature`, refactor `seal_event` onto them
   (byte-format test against the old path), plus private-key round-trip helpers.
2. **KeyStore + BrokerHome + grants.** FileKeyStore atomicity and modes, scope-name resolution, descendant
   coverage, revoke, grant digest.
3. **Framing + transport + handshake.** Unix socket and Windows pipe, peer-uid check (injectable), mutual
   challenge-response, impostor broker, replay and nonce tests.
4. **Detection.** Rules, redaction, corpus budget test, runtime-generated positives.
5. **Query port + index + heads.** `query.py` (with tests ported from the behaviour of `recall_memory`'s
   analyzer and scorer on in-test examples), `vectors.py` port, AgentIndex, snapshots, conflict heads.
6. **Replica + audit + service + client.** Dispatch, exact schemas, two-step writes, revise rules,
   secret/cross-profile filters, outbox, `sync_once`.
7. **CLI + end-to-end isolation and offline tests** (gates below), README section "Broker" and a FORMAT.md
   appendix for the broker wire messages and state file, then the REPORT.

## Acceptance gates

### Isolation (must each have a dedicated test the reviewer can mutation-test)

- **P3-1 Grant enforcement:** agents A (`work/client-a`) and B (`work/client-b`) run as **separate OS
  processes under the same OS user** against one broker over the real socket or pipe. With events containing
  the same distinctive terms in both scopes, A's recall returns only `client-a` events and B's only `client-b`
  events. A recall, write or revise naming `work/client-b` from A returns `denied`. A grant on `work` reads
  `common`, `client-a` and `client-b`; a grant on `client-a` reads neither `work` root events nor `common`.
- **P3-2 Credential binding:** A presenting B's certificate without B's key fails; replaying a captured auth
  message on a new connection fails (nonces); a certificate issued by another device or account fails; a
  revoked agent's open session gets `denied` on its next request; a foreign peer uid (injected) is refused
  before the handshake.
- **P3-3 Impostor broker:** a client whose pinned device key does not match the broker refuses to send its
  auth message.
- **P3-4 Separate indexes:** after building, A's in-memory index contains no row from any scope outside A's
  grant (inspected directly), and the two agents' index objects and snapshot files are distinct. Swapping A's
  snapshot file into B's path makes B rebuild (AAD mismatch) and never load A's rows. Two agents with
  **identical** grants still get distinct index objects.
- **P3-5 No plaintext at rest:** after a session that writes and recalls marker strings (including in body,
  tags and task), no file under the broker home or replica contains any marker, as UTF-8 or UTF-16 bytes.
  The key-store file is excluded from the marker scan but checked to contain no markers.
- **P3-6 Agents never receive data keys:** capture every byte the broker sends to the agents in a full session
  (whoami, recall, remember, revise, sign, errors). None contains any scope private key, `index_master_key` or
  `audit_key` in raw, hex or base64url form. Every response matches its exact field set. The agent key file
  contains only the agent key, its certificate, the device public key and the address.
- **P3-7 Every query is logged:** each request and each handshake outcome (ok, denied, invalid,
  unauthenticated) appends exactly one audit line before its response. The query text appears nowhere in the
  audit file, while the same query gives the same `query_hmac`. When the audit append fails, the request
  fails with `unavailable` and returns no results.
- **P3-8 Secret and cross-profile:** a `secret`-labelled event is withheld from an agent without
  `allow_secret` and returned with it. A multi-profile recall without `allow_cross_profile` is `denied`; with
  it, it is audited `cross_profile: true`. Nothing is written to `common` unless the scope is named explicitly.

### Behaviour

- **P3-9 Offline:** with a remote that raises on every call, recall, remember, revise and whoami work and the
  outbox grows. `sync_once` against a working filesystem remote then empties the outbox. A second broker home
  that shares the keys (test helper; enrollment is Phase 4) syncs from that remote, verifies, indexes and
  recalls the events. A tampered remote event is counted invalid and never stored.
- **P3-10 Detection budget:** every generated positive for each secret rule is detected and redacted, with
  label `secret`. Every PII positive raises the label to `internal` without redaction. On the benign corpus,
  secret findings are ≤ 1 % of lines and PII findings ≤ 5 %; the test prints the measured rates. Redacted
  content never contains the original span; responses and audit list kinds only.
- **P3-11 Revisions and conflicts:** a revise across scopes is denied. A lowering revise is refused. Two
  concurrent revisions of one event (from two agents) give one result with `conflict: true` and one
  alternative. Revising one head again keeps the conflict while the other head remains.
- **P3-12 Recall shape:** the top-level and per-result key sets equal those listed in "Recall shape". The
  ported scorer ranks a fixture the same way as the rules in `recall_memory._score` (expected order written
  out in the test, not computed by importing `memory-sync`).
- **P3-13 Robustness:** oversize frames, zero length, malformed JSON, duplicate keys, unknown ops, extra
  fields, a `sign` with no pending write and a wrong signature are rejected without killing the broker; other
  sessions keep working. A session cannot sign another session's pending write.

### Whole change

- The full suite passes from the repository root and from inside `profile-log/` with the prepared
  interpreter. The socket and pipe tests pass outside the Codex sandbox (reviewer run) and on the three-OS CI.
- `git status` shows changes only under `profile-log/` and the REPORT file.
- No real credentials, network access or user data used.
- The REPORT maps every gate to its test(s), lists deviations and open questions, and states measured
  detector false-positive rates.
