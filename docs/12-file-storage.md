# How files are stored

UMedia does not keep file bytes in the core. The core keeps the **library**
(identity, folder tree, ACL). Bytes always live on a **storage provider**
— including a local disk mount. Schema tables are in
`docs/11-dual-layer-library.md` and `docs/04-data-model.md`; this note is
the operational logic.

```
UI / S3 gateway / share link
            │
       MediaFile              ← ours (SQLite): name, folder, ACL, uid
            │  media_file_objects  (v1: one primary)
       StorageObject          ← index: which connection, which remote id
            │  content_reference   (the provider's own key / path / message id)
         plugin               ← adapter only; no library rules
            │
     local disk / S3 / Drive / Telegram
```

The S3-compatible gateway on this product is **not** a storage provider.
It is a protocol adapter over MediaFile. Object keys are the library path
(with ` (2)` suffixes on collisions), not `{uid}/{filename}` and not the
provider path.

## What is ours

SQLite holds three things.

**MediaFile** is what the user browses at `/files`. The folder tree lives
**only** here (`name` + `parent_id`). A row is `file` or `folder` (open
vocabulary). Folders have no StorageObject and trigger no plugin call.
`uid` is stable (`/files/{uid}`, `/f/{uid}`). `(owner_id, parent_id, name)`
is **not** unique: two library files may share a logical path.

**StorageObject** is 1:1 with bytes (or a provider-side folder/message) on
one connection. Identity on the provider is `content_reference`, not our
uid. There is no user `parent_id`. Size, hash, and MIME are stored here
and joined onto MediaFile for API responses.

**ProviderConnection** is one configured store: plugin type, encrypted
config, `import_existing`, `mirror_structure`. One plugin process can
serve many connections of the same type.

`GET /files` reads SQLite only. Listing never waits on a provider.

## What is the provider's

A plugin is an adapter: list / get / read / create / update / delete
against an external system. The core does not know provider internals.
The plugin never sees SQLite or the master key; decrypted connection
config is attached per call.

| Provider | Where the bytes are | Typical `content_reference` |
|---|---|---|
| `local` | Directory mounted into the container (`root_path`) | Path under that root |
| S3 / Drive / … (`rclone`) | The remote | The remote's own key or id |
| Telegram | A chat document/message | Channel + message id |

**Local is still a provider**, not “the product's own filesystem layer”.
It happens to write on the same machine. Library structure is independent
of that directory tree unless mirroring is on.

Providers must not assume a filesystem. Folders are optional. Telegram
messages are valid resources (`docs/03-provider-system.md`).

## Upload

1. Create a MediaFile (`status=processing`) with the library `parent_id`.
2. Write bytes through the plugin. For most providers that is the
   **provider root** (`CreateResourceIn.parent_id=None`) — the UI folder
   is not the remote path. **Local is the exception:** the connection is
   still instance-level (one `root_path`), but core namespaces the dump
   to `.umedia/users/{owner_id}/{media_file_uid}/{filename}` so two users
   cannot overwrite the same disk path. The plugin never sees `owner_id`;
   it only receives a parent path. Inbound sync does not import that
   `.umedia/` tree into the library, and does not missing-mark it.
3. Verify the write landed, upsert a StorageObject from the plugin's id,
   link it as `primary`, mark the MediaFile completed.

If the plugin reports a `content_reference` already linked to another
MediaFile (e.g. local overwriting the same path), v1 keeps one object and
one link.

Overwrite (S3 Put onto an existing projected key, or an explicit content
replace) keeps the same MediaFile, calls plugin `update` with
`overwrite_content`, updates the StorageObject, and appends the previous
version to MediaFile `history`.

## Folders, moves, deletes

- **Create folder** — MediaFile only. Nothing is created on disk or S3.
- **Rename / move** — SQLite by default. If the connection has
  `mirror_structure` and the plugin declares `move`, the change is
  applied on the provider first; a provider failure leaves the library
  untouched. A move is mirrorable only when the destination folder is
  itself provider-backed on the same connection. Pure library folders
  have no remote counterpart.
- **Delete (v1)** — MediaFile only (soft-delete tree, then optional
  hard-delete of the library row and its links). The StorageObject and
  the remote bytes stay. Trash is a library feature, not a remote delete.

## Import and sync

Two independent flags on `ProviderConnection`:

| Flag | Meaning |
|---|---|
| `import_existing` | On connect and `POST /providers/{uid}/sync`, walk the remote, upsert StorageObjects, and create missing MediaFiles under a connection-named library root (flat for Telegram). |
| `mirror_structure` | Reflect library move/rename of a linked file back to the provider when the plugin can move. |

| import_existing | mirror_structure | Typical use |
|---|---|---|
| false | false | Empty library; UMedia organizes; provider is a dump/target |
| true | false | Index the remote; reorganize only in UMedia |
| false | true | New writes/moves mirrored; do not pull the old remote tree |
| true | true | Import plus keep structure in sync where the plugin allows |

Inbound reconcile is one operation with two triggers: interval polling
of every **enabled** connection (`UMEDIA_SYNC_POLL_INTERVAL_SECONDS`,
default 900s) and the existing manual `POST /providers/{uid}/sync`
(always 202, including disabled connections). Both share the same
runner and `active_syncs` set. Uploads from the UMedia UI are a
separate path.

After a complete successful provider walk, StorageObjects not observed
this pass are marked `status=missing` (row kept, still visible in
Storage browse) and linked MediaFiles go to Trash. They restore to the
same MediaFile uid if the remote object returns. A failed or incomplete
listing never marks unseen objects missing.

Sync is a background job (`202`). `GET /files` does not wait for it.
If the remote is newer (mtime/size), prior indexed metadata goes into
MediaFile `history`. If ours is newer and mirroring is on, content is
pushed.

## Read path

`GET /files/{uid}/content`, `/f/{uid}`, and S3 GetObject all resolve
MediaFile → primary StorageObject → plugin stream. ACL is on MediaFile,
not on the file on disk.

S3 DeleteObject / DeleteObjects soft-delete the MediaFile (trash). The
StorageObject and provider bytes stay, same v1 rule as `DELETE /files`.

## Short rule

The core owns the index, the tree, and access. The provider owns the
bytes. Local disk is the nearest provider, not the logical layer of the
product.
