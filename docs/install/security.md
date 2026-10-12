# Signed sync

Signed sync authenticates change packets against explicitly trusted peers, helping reject forged or unauthorized changes. Begin with an [existing installation](../getting-started/quick-start) or [new empty group](./new-group), and keep its runtime path available. This page helps you inspect security readiness and resume setup; coordinated activation remains an operator procedure.

::: info
These commands configure the AgentMesh memory-sync application, not Hermes or other agents. They do not install Python, Syncthing or security dependencies, and do not start a worker.
:::

For initial local testing, the default is unsigned (`legacy`) sync; you can defer this opt-in setup. It relies on a trusted private exchange folder and does not authenticate packets. If your group requires signed sync, coordinate activation before relying on it.

## Understand the protection

Ed25519 signatures authenticate packet metadata against an approved peer key, including the group, persistent sender identifier and device identity. The sender identifier distinguishes a signing installation; it is separate from the device identity used to allocate record IDs. Signing does not encrypt memory or restrict an approved writer to particular records. Status JSON remains unsigned telemetry, and a signature check alone does not prove records were applied.

Keep private keys and trust files outside Git and the exchange. Peer proposals are not automatically trusted: verify the full fingerprint and scope through an independent authenticated channel. See [Signed sync](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SIGNED-SYNC.md) for the trust boundary and dependency setup.

## Inspect readiness

From the installed `app` directory (or the repository's `memory-sync` directory):

```sh
python3 agentmesh.py wizard-status --runtime "/path/to/runtime.json"
```

Observed excerpt from an unsigned empty test group, exit `0`:

```json
{"policy":"legacy","pairing":"unknown","wizard_step":"prerequisites","next_action":"check_prerequisites","prerequisites":{"crypto_ready":false,"exchange_ready":true,"python_ready":true},"roundtrip":"pending"}
```

Here `crypto_ready: false` identifies a missing dependency in the test interpreter. Status exit `0` means inspection succeeded, not that setup is ready. Installation/path errors exit `1`.

This is read-only. It reports policy, pairing state, wizard step, next action and prerequisite readiness. A successful status command does not mean signing is active. No global `--database` argument is needed: the existing runtime identifies the database.

## Resume deliberately

```sh
python3 agentmesh.py wizard-resume --runtime "/path/to/runtime.json"
```

Expected from `security_wizard.py`, not activated in this documentation run: numbered prerequisite, identity, pairing and probe steps with explicit confirmations. Pending setup exits `2`; blocked setup exits `1`. This command is interactive and can write private state after confirmation. It resumes identity creation, peer approval and an isolated signed probe using the existing group and device identity. Missing prerequisites leave setup pending; missing or mismatched persistent identity requires recovery rather than generating replacement keys.

::: warning
The interactive wizard can reach activation prompts. Before approving them, follow the operator's [coordinated upgrade boundary](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SECURITY-WIZARD.md#upgrade-activation-separate-coordinated): all peers must be coordinated, unsigned history drained and backups preserved. A probe receipt does not prove production memory convergence or all peers' readiness.
:::

`wizard-resume` exits `0` when signing policy is `required`, wizard state is `active`, pairing is `approved` and prerequisites are ready; incomplete setup exits `2`, and a blocked operation exits `1`. Managed workers now resolve explicit security paths or the same defaults as the wizard; a standard bootstrap needs no manual runtime patch. The wizard does not rewrite OS service definitions. Keep the same identity and follow the [security wizard reference](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SECURITY-WIZARD.md) for activation and recovery.

## Respect packet limits

Signed packet encoding and its JSON wire representation must each fit **8 MiB (8 × 1024 × 1024 bytes)**. Structure is limited to depth 64 and 100,000 encoded values, including dictionary keys. The current capture step does not automatically split a large diff. See [signed_packets.py](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/signed_packets.py) for the exact validation.

An oversized accumulated backlog will still fail after reducing `--interval`; that only changes the wait before another attempt. Preserve state for operator review instead of editing or splitting signed bytes by hand. No supported automatic backlog-splitting recovery command is provided.

Next, [control the worker](../use/worker) using the runtime and signing policy already configured for this installation.
