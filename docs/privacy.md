# Privacy

Know what AgentMesh keeps on the device, what leaves it and who can see it, and what to keep out of Git and support
posts. Read [Concepts](./getting-started/concepts) first.

## Keep private material out of Git

The repository contains code, tests and documentation, never memory. Never commit or publish:

- transcripts, recall results, memory exports and screenshots of memory;
- anything under `~/.agentmesh`: settings, the broker home (keystore, replica, indexes, audit log), agent key files,
  job state, status files and logs;
- recovery shares and the trusted copy of `account.json`, backups made with `agentmesh_log.backup`;
- bucket credentials, Access service tokens, webhook URLs, and any file holding them.

Guide pages use placeholders such as `BUCKET` and `/absolute/path/to/`; `docs/scripts/check-privacy.sh` refuses real
user paths and private addresses.

## Understand what the profile log shares

Memory leaves the device only as signed, encrypted events in your bucket. The storage provider sees object names,
sizes and timing, but no memory content, scope names, profile names or labels. Every device keeps its own encrypted
replica, and each scope has its own keys, so a device or agent reads only what it was granted.

Settings, agent key files, indexes and the vector cache are never synced. Each device has its own.

## Understand what ingestion sends

Transcripts stay on the device; AgentMesh never uploads them. The summary job sends bounded windows of user requests and
assistant responses to Claude, through the Claude Code CLI with no tools, MCP servers or session persistence, and
masks secrets before sending. What Claude returns becomes memory in the scope the project is routed to. Ingestion
works only on your personal home and refuses a team workspace home, so your transcripts never reach other people's
scopes. See [Ingest agent sessions](./use/ingest).

## Protect keys and the recovery kit

- **Device keys** are sealed in `keystore.sealed`, with the sealing key in the OS key store (login Keychain, Credential
  Manager or the Secret Service). This protects copied files and backups, not against malicious code running as your
  user.
- **Agent key files** in `~/.agentmesh/agents/` hold an agent's signing key, not scope keys. Keep them owner-only
  (`0600`); the [menu bar app](./use/control-app#check-keys-and-devices) warns when one is not. Give each key only to
  the agent it is for.
- **The recovery kit** has three shares; any two restore the account and can decrypt its old recovery objects. Keep
  the shares apart from each other and outside `~/.agentmesh`, and test them with `agentmesh-log recovery test`.
- **Backups** made with `python -m agentmesh_log.backup create` are encrypted to this device and the account recovery
  key. No plaintext archive touches the disk.

Removing a device rotates every key it could read, but it keeps what it already read. Revocation cannot retract data.

## Understand what AI apps see

An app connected through the [MCP gateway](./use/recall#connect-an-ai-app) sees what it recalls and remembers, and so
does its vendor. Cloudflare carries the requests. Stored memory stays ciphertext, and the gateway holds no scope or
broker keys. Keep each app's grant narrow, and approve only connection requests you started.

## Understand hybrid recall exposure

[Hybrid recall](./use/recall#use-hybrid-recall) is opt-in. It sends query text and eligible stored row content to Ollama on the embedding host (one always-on device running Ollama) to create embeddings. Background backfill sends eligible content from the broker's per-agent indexes, not just the results of your current query. Query prefetch happens before the broker's full request authorization checks; do not put secrets in a query on the assumption that a later denied request prevents embedding.

By default, only `general` and `internal` rows with nonempty content of at most 32,768 characters are embedded. `secret` rows remain keyword-only unless the owner explicitly adds `secret` to `semantic.embed_labels`. This label rule applies to stored content, not query text. Internal content can include PII; the default is not a promise of anonymization. Ranking still uses only candidates authorized for the requesting agent and its selected filters.

The embedding host connects over loopback to `127.0.0.1:11434`. Other devices use your own Access-protected hostname (shown here as `https://embed.example.com`) through a dedicated Cloudflare Tunnel with Access Service Auth. The tunnel exposes only `/api/embed` and `/api/tags`, with other paths refused; Ollama itself stays bound to loopback. TLS terminates at Cloudflare's edge, so Cloudflare can see remote query text and embedded content in plaintext. Access authentication restricts callers; it does not provide end-to-end encryption from the client to Ollama.

Keep Access credentials in the OS credential store. Only their `keychain:` or `wincred:` reference and the endpoint belong in device-local `settings.local.json`; neither field is accepted in synced settings. Never put credential values in settings, command lines, logs or support posts.

Treat vectors as memory content: embeddings can reveal information about their source text. Each device keeps its own `index/vectors-*.snap` cache, backed by RAM-only SQLite and encrypted on disk with ChaCha20-Poly1305. HKDF derives keys from the index master key, bound to provider, model, model digest and metric; content lookups use HMAC-SHA256 rather than unkeyed hashes. Query vectors stay in a bounded RAM cache. AgentMesh never syncs, exchanges or backs up the vector cache; it rebuilds missing or invalid caches. Exclude these files from external backup tools too—AgentMesh cannot prevent a full-system backup from copying them. Encryption protects copied files, not malicious code running as the same user.

Ollama runs under the host's OS user. `OLLAMA_DEBUG` logs may contain inputs, so keep debug logging off for private use and protect existing logs. Keep the model tag frozen while the broker uses it. The adapter pins the installed digest and checks it before and after every embedding batch, rejecting a detected change. It cannot detect an **ABA swap**, where model A changes to B and back to A between those checks. Cache encryption and digest checks do not remove this same-user trust boundary.

## Understand memory tree pages

[Memory tree](./use/recall#browse-the-memory-tree) pages are summaries, so a single page can reveal more than any one item. AgentMesh limits them as follows:

- `memory build` sends the newest eligible items of one node to Claude (`claude --print` with no tools, MCP servers or session persistence). Only labels listed in `memory_tree.summarize_labels` are sent, `general` and `internal` by default. Secret items are never sent, and the broker refuses any page that cites a secret item.
- Pages are ordinary encrypted events in the scope they describe. They sync and back up like other memory and are never written to disk as plaintext.
- A page's label is at least the highest label among its sources, and it never goes down automatically.
- A page summarizes only its own scope. The root and profile listings are assembled per request from the scopes that agent can read, so an agent never learns the name of a project it cannot read.
- Page text is model output over stored memory. Treat it as data, like any recalled text.
- **Export is the one plaintext copy.** `control memory-export` writes readable `.md` files because you asked for them, after a presence check for that export. It never includes secret items, includes `internal` only with `--include-internal`, writes owner-only files, and refuses folders that sync or index themselves unless you override it. After that, the folder is yours to protect and delete.
- The Memory window in the control app shows content only while it is unlocked and keeps nothing on disk.

## Handle status and screenshots carefully

`~/.agentmesh/profile-log-status.json`, `sync-status.json` and the job status files are content-free: names, device IDs,
counts, timestamps, hashed agent references and passkey labels. They still describe your setup, so treat them like
other private files. Insight shows only counts and states, but keep its screenshots private too.

Next, use the [privacy-safe help checklist](./troubleshooting#request-help-without-sharing-memory) if you need support.
