# Provider Synchronization and Reconciliation

> **Superseded / not in scope this pass** — see
> [`09-tasks.md`](./09-tasks.md) Backlog (indexer/search phase). Kept for
> reference; this reconciliation design is worth revisiting once the
> indexer phase starts, reframed around `Resource` instead of
> `MediaFile`/`StorageObject`.

Provider synchronization is a core subsystem. It keeps UMedia's logical view
consistent with changes made directly in remote storage.

## Triggers

- APScheduler periodically schedules synchronization for eligible connections.
- A user can request synchronization by creating a sync run:
  `POST /api/v1/provider-connections/{uid}/sync-runs`.
- Only one active sync run is allowed per provider connection.

## Provider capabilities

Providers declare one of these discovery modes:

- `incremental_changes`: consume a provider change feed using a durable cursor.
- `snapshot_listing`: recursively list remote objects and compare snapshots.
- `none`: provider cannot discover remote changes; only UMedia-originated
  operations can be tracked.

The provider adapter only returns normalized remote entries or change events.
It does not create MediaFile records or apply reconciliation policy.

## Persistent state

Each provider connection stores:

- synchronization mode and interval
- last successful synchronization time
- durable provider cursor/checkpoint
- last observed error

Each sync run stores status, progress, counters, cursor boundaries, timestamps,
and error details. Remote observations are staged before reconciliation so a
partial listing cannot be mistaken for mass deletion.

## Reconciliation

The synchronization service joins normalized remote identity with
`StorageObject(provider_connection_id, remote_id)` and falls back to a
provider-normalized remote path when no stable ID exists.

It classifies observations as:

- discovered: create a StorageObject and a corresponding imported MediaFile
- changed: update physical metadata and relevant logical metadata
- moved or renamed: update remote identity while preserving MediaFile identity
- missing: mark the StorageObject as missing after a complete successful scan
- restored: reactivate a previously missing StorageObject
- conflict: retain both versions and expose the conflict for user resolution

Missing objects are not immediately deleted. A grace period and repeated
successful observations are required. If another healthy StorageObject exists,
the MediaFile stays available. If none exists, it becomes unavailable but keeps
its stable identity, shares, history, and metadata.

## Safety

- Cursor advancement and reconciliation commit together.
- Failed or incomplete scans never mark unseen objects as missing.
- Sync is idempotent and resumable.
- Provider rate limits use bounded concurrency and backoff.
- Manual and scheduled runs share the same service and job model.
- Reconciliation operations are audited.
