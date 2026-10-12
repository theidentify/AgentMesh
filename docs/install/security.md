# Signed sync

Signed sync authenticates change packets against explicitly trusted peers, helping reject forged or unauthorized changes. Begin with an [existing installation](../getting-started/quick-start) or [new empty group](./new-group), and keep its runtime path available. This page helps you inspect security readiness and resume setup; coordinated activation remains an operator procedure.

::: info
These commands configure the AgentMesh memory-sync application, not Hermes or other agents. They do not install Python, Syncthing or security dependencies, and do not start a worker.
:::

For initial local testing, the default is unsigned (`legacy`) sync; you can defer this opt-in setup. It relies on a trusted private exchange folder and does not authenticate packets. If your group requires signed sync, coordinate activation before relying on it.

## Understand the protection

Ed25519 signatures authenticate packet metadata against an approved peer key, including the group, persistent sender identifier and device identity. The sender identifier distinguishes a signing installation; it is separate from the device identity used to allocate record IDs. Signing does not encrypt memory or restrict an approved writer to particular records. Status JSON remains unsigned telemetry, and a signature check alone does not prove records were applied.

Keep private keys and trust files outside Git and the exchange. Peer proposals are not automatically trusted: verify the full fingerprint and scope through an independent authenticated channel. See [Signed sync](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SIGNED-SYNC.md) for the trust boundary and dependency setup.

::: warning
Before revoking a peer key, follow the operator’s [rotation and revocation procedure](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SIGNED-SYNC.md#rotation-and-revocation). Receivers recheck signatures before checking whether a packet was already accepted. Retained packets from a revoked key therefore become invalid on the next cycle, including previously applied packets, and this permanent failure stops a managed worker. Preserve packets, receipts/history, database backups and trust state. The code provides no general retained-packet recovery command; agree on operator review before resuming, and never delete history to make status appear healthy.
:::

## Inspect readiness

From the installed `app` directory (or the repository's `memory-sync` directory):

```sh
python3 agentmesh.py wizard-status --runtime "/path/to/runtime.json"
```

For an unsigned empty test group (exit `0`):

Output (run on macOS)

```json
{"policy":"legacy","pairing":"unknown","wizard_step":"prerequisites","next_action":"check_prerequisites","prerequisites":{"crypto_ready":false,"exchange_ready":true,"python_ready":true},"roundtrip":"pending"}
```

Here `crypto_ready: false` identifies a missing dependency in the test interpreter. Status exit `0` means inspection succeeded, not that setup is ready. Installation/path errors exit `1`.

This is read-only. It reports policy, pairing state, wizard step, next action and prerequisite readiness. A successful status command does not mean signing is active. No global `--database` argument is needed: the existing runtime identifies the database.

## Resume deliberately

```sh
python3 agentmesh.py wizard-resume --runtime "/path/to/runtime.json"
```

Output (example from code)

Numbered prerequisite, identity, pairing and probe steps with explicit confirmations. Pending setup exits `2`; blocked setup exits `1`. This command is interactive and can write private state after confirmation. It resumes identity creation, peer approval and an isolated signed probe using the existing group and device identity. Missing prerequisites leave setup pending; missing or mismatched persistent identity requires recovery rather than generating replacement keys.

::: warning
The interactive wizard can reach activation prompts. Before approving them, follow the operator's [coordinated upgrade boundary](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SECURITY-WIZARD.md#upgrade-activation-separate-coordinated): all peers must be coordinated, unsigned history drained and backups preserved. A probe receipt does not prove production memory convergence or all peers' readiness.
:::

`wizard-resume` exits `0` when signing policy is `required`, wizard state is `active`, pairing is `approved` and prerequisites are ready; incomplete setup exits `2`, and a blocked operation exits `1`. Managed workers now resolve explicit security paths or the same defaults as the wizard; a standard bootstrap needs no manual runtime patch. The wizard does not rewrite OS service definitions. Keep the same identity and follow the [security wizard reference](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SECURITY-WIZARD.md) for activation and recovery.

## Check trust in every direction

Trust is local to each receiver; approval does not pass through another device. In a three-device group, verify all six directions: A accepts B and C, B accepts A and C, and C accepts A and B. Confirm each sender’s key independently and verify actual receipt in every direction. A↔B and A↔C do not establish B↔C trust.

The wizard records one selected peer pair. Its `pairing: approved` and round-trip result describe that recorded pair, not all trust relationships or delivery across the group. Use the operator’s [explicit pairing procedure](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SIGNED-SYNC.md#explicit-pairing-cli) for each receiver/sender relationship.

## Respect packet limits

Two representations must each fit **8 MiB (8 × 1024 × 1024 bytes)**: the internal typed binary encoding used for signing and the UTF-8 JSON bytes written to the packet file. File size only measures the latter; a smaller file does not prove the internal encoding fits. Structure is limited to 64 levels of nesting and 100,000 encoded values, counting dictionary keys as values. The validator checks these limits; users need not count them manually. Its errors include `encoded packet too large`, `packet too large` and `packet structure limit exceeded`, though worker reports can expose only the error class.

A **diff** is the set of changed database records captured together, not a Git diff. The current capture step does not automatically split a large diff. See [signed_packets.py](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/signed_packets.py) for the exact validation.

A **backlog** is local record changes or captured outgoing packets still waiting to be published. An oversized accumulated backlog will still fail after reducing `--interval`; that only changes the wait before another attempt. Preserve a consistent database backup (including outbox/history), original transcripts, packets, runtime/workflow and private signing/trust state. Report the action, exit code and error class through the [help template](../troubleshooting#request-help-without-sharing-memory), keeping raw material private. No supported automatic backlog-splitting recovery command exists in the code; do not edit or split signed bytes by hand.

Next, [control the worker](../use/worker) using the runtime and signing policy already configured for this installation.
