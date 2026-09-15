# Dual-layer model: StorageObject + MediaFile

Operational walkthrough (upload, folders, delete, sync, what is local):
[`docs/12-file-storage.md`](./12-file-storage.md).

Execution plan (Phase L). Naming follows root `AGENTS.md` and the
original `apps/media` split (`ObjectMetaData` / `FileMetaData`).

| Layer | Name | ≈ old media | Role |
|-------|------|-------------|------|
| Physical / provider index | **`StorageObject`** | `ObjectMetaData` | 1:1 with bytes/keys on a provider |
| User library | **`MediaFile`** | `FileMetaData` | What users browse; folder tree **only here** |

```
Frontend (/files) ──► MediaFile tree ──► link ──► StorageObject ──► plugins
                           ▲
                  list = local DB only (never live provider I/O)
```

## Provider connect flags (orthogonal)

Both are independent booleans on `ProviderConnection`:

1. **`import_existing`** — On connect (and later sync): read remote objects
   into `StorageObject`s and place corresponding `MediaFile`s under a
   connection root in the user library. If false, preexisting remote
   objects are not imported (uploads from UMedia still create both layers).
2. **`mirror_structure`** — Library folder/move/rename that touches a linked
   file is reflected on the provider when the plugin supports folders.
   If false (or provider is flat, e.g. Telegram), structure lives only in
   UMedia.

| import_existing | mirror_structure | Typical use |
|-----------------|------------------|-------------|
| false | false | Blank library; UMedia organizes; provider is dump/target |
| true | false | Index remote into library; organize freely in UMedia only |
| false | true | New uploads/moves mirrored; don't pull old remote tree |
| true | true | Full bidirectional-ish: import + keep structure in sync |

## Schema

### `storage_objects`

`uid`, `provider_connection_id`, `content_reference` (unique per
connection), `provider_parent_ref` (nullable), `type`, `name`,
`content_hash`, `content_type`, `size`, `metadata` JSON, `status`,
`last_seen_at`, soft-delete + timestamps.

No user `parent_id`.

### `media_files`

`uid` (also `/f/{uid}`), `owner_id`, `permissions`, `workspace_id`,
`type` (`file`\|`folder`\|…), `name`, `parent_id` (library tree only),
`provider_connection_id` (folders are bound to one storage; files may
denormalize the same from the primary StorageObject),
`public_permission`, `access_at`, `deleted_at`, `status`, `error`,
`history` JSON (version snapshots of linked storage when content
replaced). Per-user **stars** live in `media_file_stars`
(`user_id` + `media_file_id`), not as a column on `media_files`.

Folders need no `StorageObject`. Uploads and nested folders follow the
parent folder's storage. Root placement is `instance_settings`
(`default` / `fill_order` / `most_free`).

### `media_file_objects` (link)

`media_file_id`, `storage_object_id`, `role` (`primary`\|`replica`;
v1 = `primary` only). A StorageObject may link to **multiple** MediaFiles
(same-storage library copy shares bytes). Import still treats “any link
exists” as already imported.

### `provider_connections` additions

`import_existing: bool`, `mirror_structure: bool`.

## API

### User library (default UI)

| Method | Path |
|--------|------|
| `GET/POST` | `/files?parent_id=&q=` |
| `GET/PATCH/DELETE` | `/files/{id}` |
| `GET` | `/files/{id}/content` |
| `PUT` | `/files/{id}/permissions` |
| `GET/HEAD` | `/f/{uid}` |

### Provider index (separate menu)

| Method | Path |
|--------|------|
| `GET` | `/providers/{uid}/objects?parent_ref=&q=&only_parent=true` |
| `POST` | `/providers/{uid}/sync` |
| `POST` | `/files/{id}/link` |

Replace `/resources/*` atomically with `/files/*` on web+API.

`GET /files?q=` performs fuzzy search across the visible library;
`parent_id` is optional boost context rather than a subtree filter while
searching. `GET /providers/{uid}/objects?q=` stays within that provider and
the optional `parent_ref` subtree. Without `q`, provider browsing uses
`only_parent=true` to return only the current level.

Both list endpoints are offset/limit paginated and return the
`{items, total, limit, offset, has_more}` envelope — parameters and
shape in `docs/05-api-design.md` "Pagination". Listing stays local-DB
only; pagination happens in SQL for the owned/parent-scoped browses.

## Sync

Triggered on connect when `import_existing` is true, later via
`POST /providers/{uid}/sync` (**202**, background job), and on an
interval (`UMEDIA_SYNC_POLL_INTERVAL_SECONDS`, default 900) for every
**enabled** connection. Manual POST always runs immediately (202), even
if the connection is disabled; the poller skips disabled connections.
Both triggers share one reconcile (`import_from_provider`) and the same
`active_syncs` set, so `GET /providers/{uid}/sync` reports running vs
idle for either.

Walks the provider (plugin `list`), upserts `StorageObject`s, and creates
missing `MediaFile`s under a connection-named library root (flat for
Telegram). After a **complete successful** walk, index rows not observed
this pass are marked `status=missing` (the StorageObject row stays; it
is not `is_deleted`) and their linked MediaFiles are sent to Trash
(soft-delete, same cascade as a user trash of a folder — never
hard-deleted). A listing error (or any exception during the walk)
propagates and **does not** mark anything missing — a partial scan must
not look like mass deletion. If a previously-missing object reappears,
the same MediaFile uid is restored (stars/shares survive). A MediaFile
the user trashed while the StorageObject was still `active` is left in
trash.

- Per file: store a streamed **SHA-256 `content_hash`** when unknown or
  when size/`mtime` changed; reuse the prior hash when unchanged.
  Uploads hash at write time the same way. Folders keep `content_hash`
  null.
- If the remote is newer (mtime/size), prior indexed metadata is appended
  to the linked MediaFile `history`; if ours is newer and
  `mirror_structure` is on, content is pushed to the provider.
- Mirror when `mirror_structure` + capability: library move → plugin update.
- `GET /files` never waits on sync.

## Migration from `resources`

Create new tables; for each resource row with `content_reference` create
StorageObject + MediaFile + link; folders → MediaFile only; backfill
`legacy-unassigned` → first admin in same DB (`localuser`); drop writing
to `resources` (table may remain until cleanup migration).

## TDD order

1. Repos for StorageObject / MediaFile / link
2. Upload → StorageObject + MediaFile
3. Import on/off; mirror on/off
4. Routes + web cutover + Add Storage checkboxes
5. Stub “Storage” / provider-objects browse page
6. Preview smoke

## Out of scope here

Drive-like Hom/Starred/dual-pane; workspace UI; multi-replica; live
Telegram/S3 extra verification.
