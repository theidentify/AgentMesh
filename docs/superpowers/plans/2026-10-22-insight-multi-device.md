# Insight multi-device: device heartbeats, per-device rows, pinned Insight tunnel

Owner request (2026-10-22): show each device's name, platform, last seen and last sync in Insight, and pin `insight.krisb8.com` to one machine.

## Facts on main (f54197c)

- Phase 4 device records (`registry/devices/<id>.json`, `agentmesh-device-record-v1`) contain only the device certificate and the device's age recipient. There is **no name or platform** anywhere. So name and platform must travel inside a new sealed record.
- Registry records use `registry_store.signed(kind, private, ...)` (Ed25519, domain `AgentMesh/<kind>/Ed25519/v1\0`) and `shape()`. Sealed payloads use `agekeys.encrypt` and `b64`/`decode_ciphertext`, as in `scope-meta`.
- The broker sync loop is `broker/service.py` `sync_once`/`sync_attempt`, with `on_sync` → `status.synced`. PR #90 also refreshes `overview` (`status.overview(state)`) at most once a minute.
- Replica sync copies only `events/` and `keys/`. Registry objects are written straight to remote storage.
- Insight reads `~/.agentmesh/profile-log-status.json` through `insight/adapters.py` `profile_log_status()`. Today a device row is `{device_id, revoked}`.
- Tunnel `agentmesh-bridge` (remote-managed) currently also serves `insight.krisb8.com` → `127.0.0.1:51877`. Once Windows runs a connector for the same tunnel, Cloudflare would load-balance Insight across machines.

## Part A — device heartbeat (profile-log)

Record kind `device-heartbeat`, added to `registry_store.SCHEMAS`:

```
{format: "agentmesh-device-heartbeat-v1", device_id, issued_at, age, signature}
```

- **Path:** `registry/heartbeats/<device_id>.json`. Overwritten in place; one object per device.
- **Signature:** by the issuing device's Ed25519 key, domain `device-heartbeat`.
- **`age`:** base64 of one age ciphertext addressed to every **non-revoked** device's `age_recipient` in the registry, including the issuer. Add a multi-recipient helper next to `agekeys.encrypt`.
- **Sealed plaintext:** `wire()` JSON with exactly these fields:
  - `device_id`: must equal the outer `device_id`.
  - `name`: 1–64 chars, printable, no control characters.
  - `platform`: one of `macos`, `windows`, `linux`, `ios`, `android`, `other`.
  - `app_version`: short string or null.
  - `last_sync_at`: ms or null.
  - `last_sync_outcome`: one of the existing sync status outcomes, or null.
- **Name source:**
  - Optional `settings.json` `device.name`, validated.
  - Otherwise `socket.gethostname()` with a trailing `.local` removed, truncated to 64 characters.
  - Platform comes from `sys.platform`.

**Publish** (broker, after a sync attempt):
- When to publish: at most once every 15 minutes, **or** when `last_sync_outcome` changes, **or** when the recipient set changes (a device joined or was revoked).
- Failure handling: never block or fail a sync. Swallow errors, and only count them in the sync row if the existing schema allows; otherwise just log the class.

**Read** (broker, at most once every 15 minutes, cached between reads):
- `LIST registry/heartbeats/` then `GET` each object.
- Accept a record only when:
  - `shape()` passes;
  - the path matches the device;
  - the signature verifies with `registry.device_public(device_id, at=issued_at)`;
  - the device is not revoked;
  - `issued_at <= now + 10 min`;
  - the payload decrypts with this device's age identity;
  - the inner schema is valid and the inner `device_id` equals the outer one.
- Otherwise ignore the record. A heartbeat sealed before this device joined can't be decrypted; that device shows as "never reported" until its next publish.

**Status (`status.overview`):** each device row becomes:

```
{device_id, revoked, this_device, name, platform, last_seen_at, last_sync_at, last_sync_outcome}
```

- `last_seen_at` is the heartbeat's `issued_at` as ISO-8601.
- For this device, use the live local values. Other devices come from verified heartbeats; without one, the fields are null.
- Update `status.valid()` to match.
- Names and platform exist in plaintext **only** in this local status file. Never in R2, never in the Worker.

**R2 cost** (document it in the PR), per device per month:
- Writes: ≤ 96 PUT/day ≈ 2.9k Class A.
- Reads: LIST every 15 min ≈ 2.9k Class A, plus N × 2.9k Class B GETs.
- Total for 3 devices: ≈ 17k Class A and 26k Class B per month. Far below the free tier (1M Class A, 10M Class B).

**Gateway:** the Worker must ignore `registry/heartbeats/`. Confirm the gateway tests still pass. `verify_remote` must accept the new objects: they are encrypted, so marker hits must stay at 0.

## Part B — Insight

- `insight/adapters.py`: parse the new row fields strictly.
  - `name`: string up to 64 characters, else null.
  - `platform`: from the enum.
  - Times: ISO strings.
  - Outcome: from the enum.
  - `this_device`: bool.
- `insight/index.html`:
  - **Sync page, "Devices" card:** one row per device showing:
    - the name (or a 12-character ID when there is no name);
    - the platform;
    - a "this device" pill;
    - "last seen <relative>";
    - "last sync <outcome> <relative>";
    - "never reported" when there is no heartbeat;
    - a revoked pill.
  - **Profile log page, Devices table:** the same columns.
  - Escape everything.
- **Remote:** no new paths. `/api/profile-log` is already on the allowlist. Names and platform are metadata, and the owner approved them. Memory content stays local.

## Part C — pin insight.krisb8.com to one machine

- New tunnel, used only for this Mac's Insight (owner creates it in the dashboard): `agentmesh-insight-mac`. It publishes `insight.krisb8.com` → `http://127.0.0.1:51877`. Remove that hostname from `agentmesh-bridge`, so the bridge tunnel stays multi-connector for the gateway only. The Access app and its AUD stay the same, because the hostname doesn't change.
- `insight/tunnel/run-insight-tunnel.sh`:
  - Reads the token from the macOS Keychain (`security find-generic-password -s org.agentmesh.insight.tunnel -a agentmesh -w`).
  - Passes it to `cloudflared tunnel --no-autoupdate run` via the `TUNNEL_TOKEN` environment variable, never in argv. The token is never echoed or logged.
  - Exits non-zero with a clear message when the token or cloudflared is missing.
- `insight/tunnel/org.agentmesh.insight-tunnel.plist` template: KeepAlive, RunAtLoad, logs in `~/.agentmesh/status/`, and no token inside.
- `insight/README.md` gets the owner steps and rollback. Rollback is: add the hostname back to `agentmesh-bridge`, then bootout the plist.
- A later Windows Insight gets its own hostname (`insight-<name>`); note it, don't build it.

## Gates (each needs a test that fails if the property breaks)

- **MD-1:** a heartbeat round-trips (sign → seal → verify → open). Every non-revoked device can open it; a non-device age identity can't.
- **MD-2:** a revoked device's heartbeat is ignored, and a revoked device is not a recipient of the next publish.
- **MD-3:** tampered input is rejected:
  - a bad signature;
  - changed `age` bytes;
  - a path/device mismatch;
  - an inner `device_id` mismatch;
  - `issued_at` in the future (beyond 10 min);
  - an invalid name or platform.
- **MD-4:** throttling — at most one publish per 15 minutes unless the outcome or the recipient set changes. Read results are cached for 15 minutes. A publish failure never fails or changes a sync's outcome.
- **MD-5:** status rows carry all the fields; `this_device` is set; a device without a heartbeat has null fields. `status.valid()` accepts the new rows and rejects malformed ones.
- **MD-6:** the Insight UI renders rows for 1 and 3 devices, including "never reported" and revoked. A name like `<img src=x onerror=1>` is escaped.
- **MD-7:** the Insight remote allowlist (`remote_access.ALLOWED_PATHS`) is unchanged; the existing tests still pass.
- **MD-8:** no plaintext device name ends up in any object written to storage (marker test). The gateway Worker tests still pass.
- **MD-9:** the tunnel script never puts the token in argv or output (test with a fake `security` and a fake `cloudflared` on PATH). The plist template contains no token.

## Out of scope

- Changing the device-record or certificate format.
- Building Windows Insight.
- Deploying.
