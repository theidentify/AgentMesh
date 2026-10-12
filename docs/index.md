---
layout: home
hero:
  name: AgentMesh
  text: Local-first memory for AI agents
  tagline: Share memory between Hermes, OMP, Codex and Claude across your devices, with no cloud memory service.
  image: { src: /agentmesh-icon.svg, alt: AgentMesh }
  actions:
    - { theme: brand, text: Get started, link: /getting-started/concepts }
    - { theme: alt, text: Install, link: /install/macos }
features:
  - title: Each device owns its memory
    details: A local SQLite database per device. Immutable change packets travel through a private Syncthing folder.
  - title: Recall with evidence
    details: Agents query past decisions, constraints and preferences, and get the original source IDs back.
  - title: No silent overwrites
    details: Independent edits to the same record are quarantined for review instead of merged blindly.
---
