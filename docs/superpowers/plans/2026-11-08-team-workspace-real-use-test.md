# Team workspace: real-use test plan

Status: plan, 2026-11-08. Covers W0–W4 and W6 as merged in `main` at `93d18bf`.
Everything below has passed unit, end-to-end-in-process and CI tests. This plan checks it on **real
devices, a real R2 bucket, real Slack, the deployed gateway and a real OIDC provider**. These are the
parts no automated test has touched.

Record each step as ✅, ❌ or ⏭️ (skipped), and note anything surprising. When something fails, keep
the full JSON output and the broker log. That is enough to reproduce it here.

## 0. Ground rules

- **Isolation.** Use **disposable test homes and a separate R2 prefix**, never your real homes
  (`~/.agentmesh/...`) or the production prefix (`v1/`). Workspace entries, invites and keys are
  immutable once synced, so a mistake in a shared production prefix cannot be undone.
- **Code version.** Run the test homes from a checkout of `main` at `93d18bf` or later, with that
  checkout's virtualenv. Your installed services keep running the code they have.
- **Presence prompts.** Approval commands ask for console confirmation. Type the word shown; no
  script should answer for you.
- **Short paths.** Use short home paths. The broker socket lives in the home, and macOS limits Unix
  socket paths to 104 bytes.

Variables used below (adjust to taste):

| Name | Mac | Windows (PowerShell) |
|---|---|---|
| Checkout | `~/code/AgentMesh/profile-log` | `C:\code\AgentMesh\profile-log` |
| CLI | `python -m agentmesh_log.cli` (from the checkout, venv active) | same |
| Alice (owner) | `A=~/amtest/alice` | — |
| Carol (contributor) | `C=~/amtest/carol` | — |
| Bob (member) | — | `$B="C:\amtest\bob"` |
| Remote flags | `R="--s3-bucket BUCKET --s3-prefix ws-test/ --s3-endpoint https://ACCOUNT.r2.cloudflarestorage.com --s3-credentials keychain:SERVICE"` | same flags, `--s3-credentials wincred:SERVICE` |

Each test home is a **separate account**. That's the point: W2 is about several accounts.

## 1. Set up three accounts

Alice, on the Mac:
```sh
cli broker init --home $A --profile acme --out ~/amtest/alice-recovery
cli broker scope add --home $A acme/mesh
cli broker scope add --home $A acme/inbox
cli workspace init --home $A --profile acme
cli workspace account --home $A          # note account, fingerprint, device
cli workspace show --home $A --profile acme   # note "workspace" (the profile ID)
cli broker sync --home $A $R
```
Bob, on Windows (his own profile must not be called `acme`):
```powershell
cli broker init --home $B --profile bobself --out C:\amtest\bob-recovery
cli broker sync --home $B <remote flags>
```
Carol, on the Mac:
```sh
cli broker init --home $C --profile carolself --out ~/amtest/carol-recovery
cli broker sync --home $C $R
```
- [ ] All three `init` and `sync` calls return `ok`, or a sync count with `errors: 0`.
- [ ] `workspace show` lists exactly one member, Alice, as `owner`.

## 2. Bob joins by fingerprint (W2)

1. Send Bob Alice's **account ID**, **fingerprint** and **workspace ID** over a channel you trust
   (in person or a call).
2. Bob, first with a deliberately wrong fingerprint, then the right one. `join` asks for presence
   (type `grant`). Use the English keyboard for every presence word: a word typed in another layout
   is refused as `presence_required`.
   ```powershell
   cli workspace join --home $B --profile-id WORKSPACE --owner-account ALICE --owner-fingerprint 0000-0000-0000-0000-0000-0000-0000-0000
   cli workspace join --home $B --profile-id WORKSPACE --owner-account ALICE --owner-fingerprint FP_ALICE
   cli broker sync --home $B <remote flags>
   ```
3. Bob sends Alice his account, fingerprint and device (from the `join` output).
4. Alice:
   ```sh
   cli broker sync --home $A $R
   cli workspace member --home $A --profile acme --account BOB --role member --root-fingerprint FP_BOB
   cli workspace grant --home $A --device BOB_DEVICE --scope acme/mesh
   cli broker sync --home $A $R
   ```
5. Bob syncs again.

- [ ] The wrong fingerprint returns `{"error": "denied"}`.
- [ ] `member` with a wrong `--root-fingerprint` (try Alice's own) returns `denied`.
- [ ] After Bob's second sync, his broker has an `acme` tree containing `acme/mesh`. Check with a
  `broker grant --read acme/mesh` on Bob's home; it succeeds only if the scope exists there.
- [ ] `workspace show` (Alice) lists Bob as `member`, with `pinned: true`.

## 3. Tasks and notes across accounts (W0, W6)

Run a broker for each home in its own terminal (`cli broker run --home $A`, and the same for Bob),
then create an agent key for each:
```sh
cli broker grant --home $A --name alice-agent --read acme/mesh --write acme/mesh
```
```powershell
cli broker grant --home $B --name bob-agent --read acme/mesh --write acme/mesh
```
A new agent's first request needs a `first_access` presence window on its home; without one every
agent command returns `presence_required`. Open it on each home after creating its key (type `open`):
```sh
cli presence open --home $A
```
```powershell
cli presence open --home $B
```
Then:
```sh
cli task create AM-1 --scope acme/mesh --title "Real-use test" --claim --next "check leases" --agent-key ALICE_KEY
cli broker sync --home $A $R
```
```powershell
cli broker sync --home $B <remote flags>
cli tasks --agent-key BOB_KEY
cli task claim AM-1 --scope acme/mesh --agent-key BOB_KEY                         # expect denied (live lease)
cli task note AM-1 --scope acme/mesh --text "Ready when you are" --agent-key BOB_KEY
cli broker sync --home $B <remote flags>
```
```sh
cli broker sync --home $A $R
cli notes AM-1 --agent-key ALICE_KEY
cli task update AM-1 --scope acme/mesh --status open --handoff "over to Bob" --agent-key ALICE_KEY
cli broker sync --home $A $R
```
Bob syncs, then runs `task claim AM-1 ... --status in_progress`.

- [ ] Bob sees AM-1 as `claimed` by Alice's agent, and his first claim is `denied`.
- [ ] Alice sees Bob's note, and its author is Bob's account.
- [ ] After the handoff, Bob's claim succeeds. Both sides then show `in_progress`, owned by Bob's
  agent, with `handoff: "over to Bob"`.
- [ ] **Race:**
  1. Bob (the owner) runs `task update AM-1 --status open`, then both homes sync.
  2. Alice and Bob each run `task claim AM-1` within a few seconds, before either syncs again.
  3. Both sync twice.

  `tasks` on **both** devices now names the same owner (the earlier claim), with `conflict: true`.

## 4. Review and quotas (W3)

```sh
cli workspace settings --home $A --profile acme --review-staged on --quota-agent 5
cli broker sync --home $A $R
```
Bob syncs, adds a note to AM-1, writes 4 facts with `remember ... --scope acme/mesh`, then creates
and releases a task of his own (`task create AM-2 ... --claim`, then `task update AM-2 --status open`;
AM-1 is Alice's after the race), then syncs again. Notes and memories count toward the
daily cap; task state changes don't, so a capped agent can still release or hand off. With his
note from step 3, the cap of 5 is reached at the third fact.

- [ ] Bob's 4th fact returns `quota_exceeded`, and the AM-2 claim and release after it succeed.
- [ ] Alice, after syncing: a plain `recall` does not show Bob's facts, and `recall --include-pending`
  shows them as `review: pending`.
- [ ] `review EVENT_ID --verdict approved --agent-key ALICE_KEY` runs through Alice's broker, so it
  needs a workspace presence window first: `cli presence open --home $A --class workspace`
  (without it, `presence_required`). Afterwards plain
  recall shows that fact as `approved`.
- [ ] Task notes and task changes are not staged.

Turn staging off again before continuing: `--review-staged off`.

## 5. Invite and contributor inbox (W3)

```sh
cli workspace settings --home $A --profile acme --inbox-scope acme/inbox
cli workspace invite create --home $A --profile acme --scope acme/inbox --role contributor --max-uses 1 --days 1 --out ~/amtest/invite.json
cli broker sync --home $A $R
cli workspace join --home $C --invite ~/amtest/invite.json
cli broker sync --home $C $R
cli broker sync --home $A $R
cli workspace invite accept --home $A --profile acme
cli broker sync --home $A $R
cli broker sync --home $C $R
cli broker grant --home $C --name drop --write acme/inbox
cli presence open --home $C        # first access of the new key
```
With Carol's broker running:
```sh
cli remember "contractor report" --scope acme/inbox --agent-key CAROL_KEY
cli recall "contractor report" --agent-key CAROL_KEY              # expect denied (write-only)
cli broker sync --home $C $R && cli broker sync --home $A $R
```
- [ ] `invite accept` admits Carol as `contributor`. Using the same token from a **new** account
  (a fourth test home: `broker init`, sync, `workspace join --invite`, sync, then `invite accept` on Alice)
  is skipped with reason `no uses left`. Don't use Bob for this: `accept` ignores uses from accounts
  that are already members, so his use is dropped without a reason.
- [ ] Carol's write succeeds, her recall is `denied`, and Alice recalls the report from `acme/inbox`.
- [ ] Carol's home has no read keys for `acme`, and only `acme` and `acme/inbox` in its tree.

## 6. Removal and key rotation (W2)

```sh
cli workspace member --home $A --profile acme --account BOB --role none     # expect "rotated" in the output
cli broker sync --home $A $R
cli remember "after removal" --scope acme/mesh --agent-key ALICE_KEY && cli broker sync --home $A $R
```
Bob syncs, tries `remember` and `recall "after removal"`.

- [ ] The removal output lists every `acme` scope as rotated, with no `not_rotated`.
- [ ] Bob's write is `denied` (stale epoch), and his recall does not show "after removal".
- [ ] Carol (contributor) can still write to the inbox after she syncs. The new public key was
  republished.

## 7. Insight

A broker whose home is outside `~/.agentmesh` writes its status snapshot beside its home, so point
Insight at Alice's test home (the snapshot is written by her running broker, at most once a minute):
```sh
python3 -u ../insight/server.py --port 0 --agentmesh-home $A
```
- [ ] Profile log → **Workspaces** shows `acme` with member, task and lease counts, and no titles,
  account IDs or text anywhere on the page.

## 8. MCP gateway `tasks` tool (W4)

This step touches production: the Worker, the Tunnel and your real broker.

1. Update the code your **real** bridge and broker run to `93d18bf` or later, as you normally do.
2. Deploy the Worker (`wrangler deploy`, see "Owner deployment runbook" in `profile-log/README.md`).
3. In Claude (or another connected app), list the tools and call `tasks`.

- [ ] `tasks` appears in the app's tool list, and calling it returns data (an empty list is fine)
  with the preamble.
- [ ] A scope outside the app's policy returns `denied`.

## 9. Slack (W6)

Use a test Slack workspace or a private test channel.

1. Create the Slack app as in "Slack adapter (W6)" in `profile-log/README.md`: Socket Mode, an
   `xapp-` token with `connections:write`, and the bot scopes and `app_mention` event.
2. Alice: `broker grant --home $A --name slack --read acme/mesh --write acme/mesh --read acme/inbox --write acme/inbox`
   (no `--allow-secret`), plus a config mapping the test channel to `acme/mesh` and a second
   channel to `acme/inbox`.
3. `workspace chat-link --home $A --profile acme --platform slack --user YOUR_SLACK_ID --account ALICE`
4. Start the adapter with the tokens in the environment (see README), and keep its terminal visible.

- [ ] `@bot help` answers in a thread.
- [ ] `@bot tasks` lists AM-1, `@bot task AM-1` shows next/handoff and notes, and `@bot recall real-use`
  returns lines.
- [ ] `@bot note AM-1 from slack` appears in `cli notes AM-1`, prefixed `[slack <account>]`.
- [ ] From a **second, unlinked** Slack user, `@bot recall x` is refused. In the inbox channel,
  `@bot remember hello` is stored.
- [ ] After `chat-link ... --account none`, your next message is refused **without restarting**
  the adapter.
- [ ] The adapter's terminal and logs contain no token text.

## 10. OIDC attestation (W1)

Requires an OAuth "desktop app" client at your provider (for example Google), with its client ID.

```powershell
cli workspace attest oidc-login --home $B --profile acme --issuer https://accounts.google.com --client-id CLIENT_ID --out C:\amtest\token.json
```
Copy the token file to Alice's Mac securely (it contains Bob's email), then:
```sh
cli workspace attest oidc --home $A --in ~/amtest/token.json --issuer https://accounts.google.com --client-id CLIENT_ID
cli workspace attest check --home $A --profile acme --email BOB_EMAIL
```
- [ ] The browser sign-in completes and the token file is written.
- [ ] `attest oidc` asks for presence and returns `ok`, and `check` returns Bob's account.
- [ ] A different `--client-id` returns `denied`. Delete both copies of the token file afterwards.

(This works after step 6 too: an attestation doesn't need current membership. Only the admin who
signs it must be an owner or admin at that time.)

## 11. Clean up

- Stop the test brokers and the adapter.
- Delete `~/amtest/` on the Mac and `C:\amtest\` on Windows (they include recovery shares).
- Delete the R2 objects under `ws-test/`.
- Remove the test homes' OS key store items: Keychain Access → items named
  `agentmesh.profile-log` whose account is a test device ID; on Windows, Credential Manager.
- Revoke the Slack app tokens, or delete the test app.

## What to send back

The checklist with ✅ / ❌ / ⏭️, and for each ❌ the command, its JSON output, and the broker log
lines around that time (`broker run --log-dir` if you used it).

## Results, 2026-11-08

- **Steps 1–6 on one Mac** (three or four accounts, a folder as the remote, `scripts/amtest_guided.py`):
  run 4 passed 60 of 62. The two failures were a mistyped presence word and a check aimed at the
  wrong task; the check is now fixed. Earlier runs found two bugs, both fixed in #181: a running
  broker ignored objects a one-shot `broker sync` downloaded, and task state counted toward quotas.
- **Steps 1–6 across machines** (Alice, Carol and Dave on the Mac, Bob on Windows, real R2 under
  `ws-test/`, `scripts/amtest_cross.py`): 64 of 69 passed. All five failures came from presence words
  typed with a Thai keyboard layout in the first attempt. Four of them passed when redone; the
  wrong-root-fingerprint check went undecided here but passed in the Mac-only run. Everything run on
  Windows passed:
  - join by fingerprint;
  - leases, notes and the claim race (same owner on both machines, `conflict: true`);
  - staging and quota (including release while over quota);
  - after removal: writes denied and new events unreadable.
- **Clean-up (11)** done on 2026-11-09:
  - R2: all 103 objects under `ws-test/` deleted;
  - Mac: test homes and test Keychain items removed;
  - Windows: `C:\amtest` and Bob's credential moved to the Recycle Bin.
- **Insight (7)**, 2026-11-09, on a disposable home with a staged workspace:
  - the Workspaces panel shows counts only: members, tasks, leases, conflicts, reviews, quota;
  - no task titles, fact text or 32-character IDs on the page or in the API.
  - This step found a bug: a test broker wrote its status into the real `~/.agentmesh`
    snapshot, so Control and Insight could show test data. It's fixed: brokers outside the
    AgentMesh home now keep their snapshot beside their home.
- **Not yet run:** 8 (gateway), 9 (Slack), 10 (OIDC).
