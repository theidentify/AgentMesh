# Signed sync

Signed sync authenticates change packets against explicitly trusted peers, helping reject forged or unauthorized changes. Begin with an [existing installation](../getting-started/quick-start) or [new empty group](./new-group), and keep its runtime path available. This page helps you inspect security readiness and resume setup; coordinated activation remains an operator procedure.

::: info
These commands configure the AgentMesh memory-sync application, not Hermes or other agents. They do not install Python, Syncthing or security dependencies, and do not start a worker.
:::

## Understand the protection

Ed25519 signatures bind packets to approved peer keys and their group, sender and device identity. Signing does not encrypt memory or restrict an approved writer to particular records. Status JSON remains unsigned telemetry, and a signature check alone does not prove records were applied.

Keep private keys and trust files outside Git and the exchange. Peer proposals are not automatically trusted: verify the full fingerprint and scope through an independent authenticated channel. See [Signed sync](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SIGNED-SYNC.md) for the trust boundary and dependency setup.

## Inspect readiness

From the installed `app` directory (or the repository's `memory-sync` directory):

```sh
python3 agentmesh.py wizard-status --runtime /path/to/runtime.json
```

This is read-only. It reports policy, pairing state, wizard step, next action and prerequisite readiness. A successful status command does not mean signing is active. No global `--database` argument is needed: the existing runtime identifies the database.

## Resume deliberately

```sh
python3 agentmesh.py wizard-resume --runtime /path/to/runtime.json
```

This command is interactive and can write private state after confirmation. It resumes identity creation, peer approval and an isolated signed probe using the existing group and device identity. Missing prerequisites leave setup pending; missing or mismatched persistent identity requires recovery rather than generating replacement keys.

::: warning
The interactive wizard can reach activation prompts. Before approving them, follow the operator's [coordinated upgrade boundary](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SECURITY-WIZARD.md#upgrade-activation-separate-coordinated): all peers must be coordinated, unsigned history drained and backups preserved. A probe receipt does not prove production memory convergence or all peers' readiness.
:::

`wizard-resume` exits `0` when policy is required, the wizard is active, pairing is approved and prerequisites are ready; incomplete setup exits `2`, and a blocked operation exits `1`. It does not rewrite services to use the security configuration. Keep the same identity and follow the [security wizard reference](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SECURITY-WIZARD.md) for activation and recovery.

Next, [control the worker](../use/worker) using the runtime and signing policy already configured for this installation.
