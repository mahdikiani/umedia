# Data Model


## Resource


Resource:

id

provider_id

type

name

parent_id

metadata

content_reference


Examples:


Telegram:

type=document

metadata:

channel_id

message_id



S3:

type=file

metadata:

bucket

key

## Concrete SQL schema

SQLite, via `fastapi_mongo_base.sql.models.BaseEntity`, which already
provides `uid`, `created_at`, `updated_at`, `is_deleted`. The dual-layer
model (`docs/11-dual-layer-library.md`): **MediaFile** is the user
library, **StorageObject** the physical provider index, linked through
`media_file_objects`.

### `provider_connections`

Ported from `apps/api/apps/provider_connections/models.py` — this table
already correctly supports multiple connections per provider type (no
uniqueness constraint on `provider_type` alone), which is what
"multi-instance" needs.

| Column | Type | Notes |
|---|---|---|
| `uid` | str, pk | |
| `owner_id` | str, indexed, required | creating user's uid; each user only lists/manages their own connections |
| `provider_type` | str, indexed | matches a loaded plugin manifest's `id` (`local` is admin-only to create and use) |
| `name` | str, indexed | user-given label, e.g. "AWS Personal" vs "Backblaze Backup" |
| `encrypted_config` | text | JSON config, encrypted at rest; decrypted only in-core, passed to the plugin per-call |
| `status` | str | `configured` / `active` / `disabled` / `error` |
| `enabled` | bool, indexed, default `true` | gates whether *this connection* may be used for file operations (`apps/media_files/plugin_gateway.py`'s single resolve choke point rejects a disabled one) -- a way to turn one off without losing its config or the objects already indexed through it. **Not** what spawns a plugin *process*: one process commonly serves many connections of the same `provider_type` (docs/03-provider-system.md), so process spawning stays a manifest-level (`plugin.json`'s own `enabled`) decision, not a per-connection one |
| `import_existing` | bool, default `false` | on connect (and explicit `POST /providers/{uid}/sync`): index the remote's preexisting objects and place MediaFiles for them under a connection-named library root (docs/11-dual-layer-library.md) |
| `mirror_structure` | bool, default `false` | reflect library moves/renames of linked files back onto the provider, when the plugin declares the `move` capability (flat providers such as Telegram never mirror) |
| `last_tested_at` | datetime, nullable | |
| `last_error` | text, nullable | |

### `media_files` (user library)

What users browse at `/files`. The folder tree lives **only** here.
Content-bearing fields (`size`, `content_type`, `content_hash`,
`content_reference`) are *not* columns -- they are resolved from the
linked primary `storage_objects` row in the same local SQL query.

| Column | Type | Notes |
|---|---|---|
| `uid` | str, pk | this is the `{uid}` in `/f/{uid}` and `/files/{id}` (preserved across the `resources` migration, so old share links keep working) |
| `owner_id` | str, indexed, required | owning user's uid |
| `permissions` | JSON | per-user ACL list of `{user_id, permission int}` |
| `workspace_id` | str, nullable, indexed | reserved |
| `type` | str | `file` \| `folder` \| `message` \| … — open vocabulary |
| `name` | str, indexed | |
| `parent_id` | str, nullable, indexed | self-referential library tree; null = root |
| `provider_connection_id` | str, nullable, indexed | folders are bound to one storage; uploads into a folder follow this (walk ancestors if a parent is unbound). Files store the same as a denormalized hint; the StorageObject join remains source of truth for linked bytes. Root placement uses `instance_settings` |
| `metadata` | JSON | app-level bag; e.g. `{"import_root": <connection uid>}` marks a connection's library root folder |
| `status` | str | `processing` \| `completed` \| `failed` |
| `error` | text, nullable | |
| `history` | JSON | version snapshots of linked storage when content is replaced |
| `public_permission` | str | `none` \| `read` — anonymous-link gate; per-user ACL is `permissions` |
| `access_at` | datetime | last-read timestamp |
| `deleted_at` | datetime, nullable | soft-delete marker; deleting a MediaFile never touches the StorageObject or the remote bytes (v1 rule, doc 11) |

### `storage_objects` (physical provider index)

1:1 with bytes/keys on a provider; written by uploads (plugin-confirmed
writes) and the import/sync job. No user `parent_id` -- structure is the
library's business.

| Column | Type | Notes |
|---|---|---|
| `uid` | str, pk | |
| `provider_connection_id` | fk → `provider_connections.uid`, indexed | |
| `content_reference` | str, indexed | opaque plugin pointer (remote key/path); unique per connection — upserts key on `(provider_connection_id, content_reference)` |
| `provider_parent_ref` | str, nullable | the *provider's own* containment (e.g. POSIX parent dir), for index browsing and mirroring |
| `type` | str | `file` \| `folder` \| `message` \| … |
| `name` | str, indexed | as last seen at the provider |
| `content_hash` | str, nullable, indexed | SHA-256 when known |
| `content_type` | str | MIME type |
| `size` | int | |
| `metadata` | JSON | provider-specific bag (e.g. `{channel_id, message_id}` for Telegram) |
| `status` | str | `active` \| `missing` — `missing` means the last **complete** inbound walk did not observe this object; the row stays (Storage browse can still show it). Reappear on a later walk sets `active` again |
| `last_seen_at` | datetime | refreshed on every upsert/sync pass |
| `deleted_at` | datetime, nullable | |

### `media_file_objects` (link)

| Column | Type | Notes |
|---|---|---|
| `media_file_id` | fk → `media_files.uid`, indexed | |
| `storage_object_id` | fk → `storage_objects.uid`, **unique** | v1: one link per object |
| `role` | str | `primary` (v1) \| `replica` (reserved) |

### `user_access_keys` (per-user signing keys)

S3-style key pairs, one or more per user (migration
`0007_user_access_keys`). Temporary share links
(`GET /f/{uid}?expires=&key_id=&sig=`, doc 05 "Direct-link alias") are
HMAC-SHA256-signed with the owning user's *secret*; the URL carries only
the public `access_key_id`. Every new account gets one `default` key at
creation (`AuthService.setup` / `create_user`); accounts predating the
table get one lazily on their first mint.

| Column | Type | Notes |
|---|---|---|
| `user_id` | str, indexed | owning `localuser` uid (opaque string, same convention as `media_files.owner_id`) |
| `access_key_id` | str, **unique**, indexed | public half: `um_` + urlsafe random (18 bytes) — safe to appear in URLs and logs |
| `encrypted_secret` | text | the urlsafe random secret (32 bytes), stored **only** as a Fernet token under the installation `CredentialCipher`; decrypted in memory at sign/verify time and returned in plaintext only once, in the create-key API response |
| `label` | str | `default` for the auto-created pair |
| `is_active` | bool, indexed, default `true` | deactivating a key immediately revokes every temporary link it ever signed (verification only accepts active keys) |

`is_deleted`/`created_at`/`updated_at`/`uid` come from `BaseEntity` on all
tables below.

### `instance_settings` (singleton)

Where root uploads and new root folders land when the parent folder is
not already bound. `GET/PATCH /settings/placement`.

| Column | Type | Notes |
|---|---|---|
| `placement_policy` | str | `default` \| `fill_order` \| `most_free` (`most_free` currently follows fill-order until plugins report capacity) |
| `default_connection_id` | str, nullable | used by the `default` policy |
| `fill_order` | JSON list of connection uids | used by `fill_order` / `most_free` |

### `operation_notifications`

Persisted reports for failed asynchronous user commands. They are scoped to
the initiating user and deduplicated by their source job or upload id.

| Column | Type | Notes |
|---|---|---|
| `uid` | str, pk | |
| `owner_id` | str, indexed | user who initiated the operation |
| `operation` | str | `upload` / `copy` / `move` |
| `item_name` | str | file or affected item label |
| `error` | text | actionable failure detail |
| `source_type`, `source_uid` | str | idempotency key for the failed job |
| `read_at` | datetime, nullable | null until the user marks it read |

### `resources` (legacy, unmounted)

The pre-dual-layer table. Migration `0005_storage_media_files` copied its
rows into the tables above (uids preserved; `legacy-unassigned` owners
backfilled to the first admin in the same-database `localuser` table) and
the `/resources/*` routes are no longer mounted; the table itself remains
until a later cleanup migration.
