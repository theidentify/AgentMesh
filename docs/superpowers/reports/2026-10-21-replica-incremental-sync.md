# Replica incremental sync: operation counts

`Replica.sync_once` no longer lists the whole bucket every cycle.

- **Incremental cycles:** `keys/` is listed in full. Each scope prefix `events/{p}/{s}/` is listed with `start_after` set to the floor ULID of `min(max seen ULID ts, now) - 10 min`. The cap at `now` stops a device with a fast clock from hiding other devices' events.
- **Full reconciliation:** runs on start, every 24 h, or with `sync_once(..., full=True)`. It finds objects written with old ULIDs, for example by a device that was offline.
- **Trailing LIST removed:** `remote_keys` is now the listing plus the successful uploads.
- **Negative cache:** `(key, sha256)` for objects that failed verification. It is cleared when the account, registry records, key epochs or local wraps change, and at reconciliation.
- **Storage API:** `Storage.list(prefix, *, start_after=None)` is additive. The filesystem backend filters; S3 sends `StartAfter`.

## Measured (`python profile-log/tests/broker/measure_sync_cost.py`)
Setup: 30,000 objects across 7 scope prefixes, 5 `keys/` objects, one new event per cycle, polling every 60 s. One S3 page holds 1,000 keys.

| Algorithm | LIST calls/cycle | Keys listed/cycle | GETs/cycle | Class A pages/cycle | Class A pages/30 days |
|---|---:|---:|---:|---:|---:|
| Old (measured) | 4 | 60,012 | 1 | 64 | 2,764,800 |
| Incremental (measured) | 8 | 20 | 1 | 8 | 345,600 |
| Incremental incl. daily reconciliation (amortized) | — | — | — | 8.02 | 346,560 |

The steady state no longer grows with total history. It is 1 LIST for `keys/` plus 1 per scope prefix, plus GETs for new objects only. `keys/` stays small (one wrap per non-root scope per key epoch; 5 in the fixture), so it is still listed in full.

## Through the broker (after #62)
#62 changed `Broker.sync_once` to stage one listing of `events/`, `keys/`, `registry/`, `keyring/`, `profiles/` and `alerts/` per cycle, outside the broker lock. After this PR:
- `events/` and `keys/` come from `Replica.prepare()` (under the lock) and `Replica.plan()` (outside it).
- The 4 control prefixes are still listed whole; they are small.
- Prefetch skips objects in the negative cache.

Calculated per cycle at 30k objects (not measured):

| | Pages/cycle | Pages/30 days |
|---|---:|---:|
| main after #62 (one staged full listing) | 30 + 1 + 4 = 35 | 1,512,000 |
| this PR | 8 + 4 = 12 | 518,400 |

## Remaining costs
- **Scopes, prefixes and devices:** cost per cycle scales with the number of scopes plus the 4 control prefixes, and the 1M free Class A per month is shared by every device on the account.
- **Late events:** an event with an old ULID (a device offline for a long time, or with a clock more than 10 min slow) is picked up only by the daily reconciliation.
- **Phase 6 outbox drain:** asked to use the same `start_after` cursor (note in the shared notes folder).
