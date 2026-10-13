# Resolve conflicts

Choose which version of a conflicting record to keep, then deliver that decision to the other devices without losing unrelated rows. Start with the [packet lifecycle](../getting-started/concepts#conflicts-affect-whole-packets), the runtime paths for the affected installations and [stopped workers](./worker). You will preview and confirm a supported resolution, check convergence and explicitly restart execution.

## Understand what is being resolved

A conflict means a packet cannot safely replace the current record. Independent edits to the same record can be resolved, including an update on one device and deletion on another, when their common origin and complete history can be proved. A natural-key collision between different records, a different baseline or a required foreign-key repair needs separate operator review; this command does not repair those cases.

![Device A and device B make conflicting edits; an operator previews a choice, publishes a resolution packet, and device B receives it before workers restart. Deleting packets is rejected.](/diagrams/resolve-flow.svg)

**A · Local edit** and **B · Conflicting edit** lead to **Review and choose** with workers stopped. **Resolution packet**, highlighted, carries the confirmed choice and all non-conflicting companion rows from the covered packets. **Receive on B** validates and applies that decision with its evidence; **Check, then restart** follows only after reviewing both devices. The rejected branch, **Never delete packets**, cannot clear a conflict safely: those files are needed to prove and replay the resolution.

Commands below run from the installed `app` directory or checkout's `memory-sync` directory. These are POSIX shell examples; on Windows use `py -3`, enter multiline commands on one line without backslashes, and set the displayed shell variables using your shell's input facility. Output excerpts omit private identifiers, digests, paths and timestamps. Values and counts are from a disposable two-device test, not expected totals for your group.

## Activate once, after upgrading every device

Upgrade **every reader and worker**, including offline devices, before enabling the new packet format. Stop older foreground workers and services through their own controls; managed stop cannot prove they have exited. Run the installed program on each device:

```sh
python3 agentmesh.py protocol-capability
```

Output (run on macOS)

```json
{"format":"agentmesh-protocol-capability-v1","protocol_capability":1}
```

This reports the program's capability, not whether its database is activated or its peers are ready. An older executable that lacks this action is not ready. Upgraded devices can read new packets before activation, but continue emitting the old format until their own database is activated. Existing packet readers are retained after activation; do not downgrade an activated installation.

Create an owner-only JSON inventory outside the exchange. Include every assigned device identity, including offline peers and reserved identities. Record the installed software you actually checked, its capability and whether the device is offline. For the two-device test, the inventory was:

```json
{
  "inventory_complete": true,
  "legacy_workers_drained": true,
  "peers": [
    {"node":"mac","capability":1,"software":"AgentMesh resolution-v1","offline":false},
    {"node":"windows","capability":1,"software":"AgentMesh resolution-v1","offline":false}
  ]
}
```

The `node` values name device identities, not operating-system restrictions. Adapt this inventory to your group; include the third identity if assigned or reserved. Setting the two acknowledgements to `true` asserts you checked the complete inventory and stopped legacy workers; the command cannot discover an unreported offline installation. Each listed peer needs capability at least `1`.

On each device you intend to activate, preview against its own runtime:

```sh
python3 agentmesh.py activate-protocol --runtime "/path/to/runtime.json" \
  --inventory "/path/to/private/peer-inventory.json" --preview
```

Output (run on macOS)

```json
{"minimum_capability":1}
```

The full output also contains `inventory` and `plan_digest`. Review it, then paste its actual digest at the `read` prompt below. Preview saves a private plan; it does not activate the database.

```sh
printf 'Activation plan digest: '
read -r ACTIVATION_PLAN
python3 agentmesh.py activate-protocol --runtime "/path/to/runtime.json" \
  --plan-digest "$ACTIVATION_PLAN"
```

Type **ACTIVATE** at the confirmation prompt. For automation, add `--yes --confirm ACTIVATE`; both the exact word and `--plan-digest` are required.

Output (run on macOS)

```json
{"next_action":"worker-start","status":"activated"}
```

Activation is local, not a broadcast approval. Repeat the preview and confirmation for each database that will emit new packets. It cooperatively stops an owned running worker and leaves it stopped. If resolving a conflict now, continue below before restarting. An error exits `1`; a declined confirmation returns `status: cancelled` and exits `2`. Do not repeat activation on an already activated database.

::: warning Keep plans private
Activation and resolution previews save artifacts in `.agentmesh-plans/` beside the runtime file. Resolution plans contain complete row content, so keep this directory and any explicit-row input outside the exchange and out of shared logs. Retain the confirmed plan for publication retries.
:::

## Stop, inspect and preview a choice

Coordinate other writers so the records remain unchanged during review. Preserve the original exchange packets and a [consistent database backup](../troubleshooting#preserve-a-consistent-database-backup). On affected devices, stop managed execution:

```sh
python3 agentmesh.py worker-stop --runtime "/path/to/runtime.json"
python3 agentmesh.py conflicts --runtime "/path/to/runtime.json" --json
```

Output (run on macOS)

```json
{"cycles":0,"state":"stopped"}
```

The first command's result above means no managed worker was running in the test. Confirm any unmanaged execution has stopped too. The conflict listing returned this excerpt:

Output (run on macOS)

```json
{"conflicts":[{"kind":"conflict","verification":"unverified preview"}],"content_shown":false,"format":"agentmesh-conflicts-v1"}
```

The full listing includes each `packet_uuid`, table and revision metadata. It is read-only: no capture, signature verification, OS unlock or live database migration occurs. `unverified preview` is a warning about that limited inspection, not permission to trust the packet. Listing includes pending and invalid diagnostics too; exit `0` only means inspection succeeded. To inspect both row values **locally**, use:

```sh
python3 agentmesh.py conflicts --runtime "/path/to/runtime.json" --show-content
```

This sets `content_shown: true` and includes `key`, `mine` and `theirs`. Do not share its output. It cannot be combined with `--json`. If diagnostics have not been refreshed, use [receive-only](#receive-on-peers-and-restart) with execution stopped, then list again; listing itself does not scan incoming files into diagnostics.

Select the packet to resolve from the full listing and choose deliberately. Repeat `--packet` to include multiple selected packets. The preview includes their necessary history and companion rows automatically.

| Choice | Result for conflicting keys |
| --- | --- |
| `--choice mine` | Keep this device's local row or deletion; refuses if there is no local side. This is the default, so specify the choice explicitly when reviewing. |
| `--choice theirs` | Keep the incoming side, including a deletion; refuses if there are multiple remote sides to choose between. |
| `--choice row --rows "/path/to/private/rows.json"` | Supply complete rows for every conflicting key. The JSON array entries contain `table`, `key` (an array of primary-key values) and `row` (all columns). A `null` row selects deletion. |
| `--choice delete` | Delete the conflicting records. It does not discard non-conflicting companion records. |

Do not use explicit rows to change primary keys, deduplicate different identities or bypass constraints. The preview rehearses the complete transaction against a private snapshot before allowing confirmation.

```sh
printf 'Selected packet UUID: '
read -r RESOLUTION_PACKET
python3 agentmesh.py resolve --runtime "/path/to/runtime.json" \
  --packet "$RESOLUTION_PACKET" --choice theirs --preview
```

Output (run on macOS)

```json
{"choice":"theirs","keys":3,"packets":2}
```

The full output includes `plan_digest`. Here the two source packets contained one conflicting key and two separate companion keys: all three were covered. Nothing has been committed to the live database yet. If you change the choice or inputs, make and review a new preview.

## Confirm and publish the resolution

Paste the reviewed preview's digest below. `review-1` is a retry identifier for this one decision; use a different identifier for a different decision.

```sh
printf 'Resolution plan digest: '
read -r RESOLUTION_PLAN
python3 agentmesh.py resolve --runtime "/path/to/runtime.json" \
  --plan-digest "$RESOLUTION_PLAN" --operation-id review-1
```

Type **RESOLVE** when asked. For automation, use the same command with `--yes --confirm RESOLVE`; the digest and stable `--operation-id` are still required.

Output (run on macOS)

```json
{"next_action":"worker-start","publish":{"published":1,"unavailable":false},"receive":{"applied":0,"conflict":0,"invalid":0,"pending":0,"unavailable":false},"status":"resolved"}
```

The full output also identifies the new resolution packet in `packet_uuid`. The chosen rows, history, disposition records and outgoing packet commit together locally; publication then sends the immutable decision. Original packets remain unchanged. Non-conflicting companion rows are kept, and previously held originals are marked superseded rather than given fictitious applied receipts.

Check `next_action` and the nested counters even when the command exits `0`. `retry-publication` means the local decision committed but publication was unavailable: restore exchange availability and retry **the same plan digest and operation ID** to publish the same packet. `review-diagnostics` means receive still reports conflict, invalid, pending or unavailable; inspect before restarting. A refusal exits `1`; declining the confirmation exits `2` without committing the proposed decision.

## Receive on peers and restart

Let Syncthing deliver the resolution **and its original evidence**. On each other device whose worker is stopped:

```sh
python3 agentmesh.py receive-only --runtime "/path/to/peer-runtime.json"
```

Output (run on macOS)

```json
{"next_action":"worker-start","receive":{"applied":1,"conflict":0,"invalid":0,"pending":0,"unavailable":false},"status":"received"}
```

`receive-only` captures local edits and imports packets under recovery locks. It also cooperatively stops an owned running worker if necessary; it does not ingest transcripts, summarize, publish the local outbox or restart execution. Failed/stopped worker metadata is preserved. An exit `0` with `next_action: review-diagnostics` is not a clean result.

On both devices, run `conflicts --runtime "/path/to/runtime.json" --json` again and [check status](./status). In the test, both listings became:

Output (run on macOS)

```json
{"conflicts":[],"content_shown":false,"format":"agentmesh-conflicts-v1"}
```

Both databases had the chosen value and both companion rows; `sync.superseded` was `2`, while `conflict`, `invalid`, `pending` and unpublished `outbox` counts were `0`. `superseded` counts distinct covered original packets, separately from `received`. Compare the actual chosen records too; equal counters alone do not prove convergence.

After review, explicitly start each managed worker. For unsigned installations:

```sh
python3 agentmesh.py worker-start --runtime "/path/to/runtime.json" --legacy-drained
```

For signed installations, omit `--legacy-drained`.

Output (run on macOS)

```json
{"cycles":1,"failure_kind":null,"last_error":null,"state":"running","warning":null}
```

This was the unsigned test's startup result. Check ongoing [worker status](./worker#use-managed-execution) as well. No activation, resolution or receive-only command automatically restarts the worker.

## Handle refusals without rewriting history

| Refusal or condition | Next step |
| --- | --- |
| `protocol activation required` | Upgrade all peers, verify the full inventory and activate this database before previewing a resolution. |
| `peer inventory omits an observed or reserved peer` | Correct the full inventory, including offline/reserved identities, and preview activation again. Do not remove exchange directories or reservations to bypass it. |
| `uncaptured local edits` | Coordinate writers, run `receive-only`, then create a fresh preview. This captures edits but does not publish the local outbox. |
| `stale resolution plan` / `runtime paths or trust changed; preview again` | Review what changed and create a fresh preview. Do not edit the saved plan. |
| `multiple remote sides; select explicit row` | Review all branches locally and provide a complete explicit choice. |
| `unsupported relational repair` | The result would violate constraints or cause unplanned row changes. Preserve evidence and request operator review; natural-key, unique-key and foreign-key repairs are outside this procedure. |
| Missing evidence, unknown baseline, different origins or an oversized proof | Retain state and obtain available original history for review. Do not invent ancestry, split an atomic resolution or delete packets to clear diagnostics. |

If two people independently resolve the same conflict differently, neither silently wins. Exchange both decisions and make a further resolution covering the divergent branches. Agreement requires a final shared decision, complete evidence delivery and compatible trust.

For signed sync, an explicitly approved [rotation manifest](../install/security#approve-the-new-key-on-every-peer) can authorize listed resolution evidence that was never committed here before revocation, including an old-key resolution packet. The UUID and complete envelope digest must match and historical signature verification must pass. This is authorization state `verified`, not `applied`: it does not create a receipt or apply rows by itself. Outside an approved manifest, never-committed evidence still requires current trust, even if previously inspected or verified. Exact committed receipts and superseded dispositions keep their historical-replay rules; altered packets remain invalid. Rotation preserves packet bytes and evidence references.

A protected signing key needs [OS unlock](../install/security#enable-unattended-os-unlock) for resolution preview/commit, confirmed activation and `receive-only`. These paths do not prompt for a passphrase or fall back to unsigned writes; a locked refusal occurs before stopping the worker or creating worker-control files. Read-only conflict listing and activation preview do not unlock the key. Enable OS unlock before making the final preview: changes to the identity or trust files invalidate the saved binding.

Next, [check sync status](./status) and verify the chosen records on each receiving device before returning to normal work.
