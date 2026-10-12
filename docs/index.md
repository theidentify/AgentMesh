---
layout: home
hero:
  name: AgentMesh
  text: Local-first memory for AI agents
  tagline: Recall shared memory from Hermes, OMP, Codex and Claude. Built-in session ingestion supports OMP, Codex and Claude; no cloud memory service is required.
  image: { src: /agentmesh-icon.svg, alt: AgentMesh }
  actions:
    - { theme: brand, text: Get started, link: /introduction }
    - { theme: alt, text: Install, link: /install/macos }
features:
  - title: Each device owns its memory
    details: A local SQLite database per device. Immutable change packets travel through a private Syncthing folder.
  - title: Recall with evidence
    details: Agents query past decisions, constraints and preferences, and get the original source IDs back.
  - title: No silent overwrites
    details: Independent edits to the same record are quarantined for review instead of merged blindly.
---
