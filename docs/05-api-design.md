# API Design


Base:

/api/v1


Resources:

GET /providers

POST /providers


GET /resources


GET /resources/{id}


GET /resources/{id}/content



Operations:

copy

move

delete


All APIs should be RESTful.

## Concrete route list

Base path: `/api/v1` (superseding `apps/media`'s current `/api/media/v1` —
`apps/media` is now the whole backend, not just a files microservice).

### Auth (`usso.lite`-backed, see `docs/02-architecture.md`)

| Method | Path | Notes |
|---|---|---|
| `GET` | `/auth/state` | `{configured, authenticated}` |
| `POST` | `/auth/setup` | first-run bootstrap admin, sets httponly cookie |
| `POST` | `/auth/sessions` | login, sets httponly cookie |
| `GET` | `/auth/sessions/current` | current session |
| `DELETE` | `/auth/sessions/current` | logout |
| `PATCH` | `/auth/password` | change password, rotates session |

`AuthState` includes an optional `user`.

### Users

| Method | Path | Notes |
|---|---|---|
| `GET` | `/users` | admin only; list users |
| `POST` | `/users` | admin only; create user |
| `PATCH` | `/users/{uid}` | admin only; update user |
| `DELETE` | `/users/{uid}` | admin only; delete user |

### S3 access keys

| Method | Path | Notes |
|---|---|---|
| `GET` | `/access-keys` | current user's active and revoked keys, newest first; secrets omitted |
| `POST` | `/access-keys` | create a key; returns its plaintext secret once, only in this response |
| `DELETE` | `/access-keys/{uid}` | revoke a key owned by the current user; missing or unowned keys return 404 |
| `GET` | `/access-keys/s3` | public S3 endpoint, region, bucket, and path-style connection settings |

### Providers

| Method | Path | Notes |
|---|---|---|
| `GET` | `/provider-types` | catalog sourced from loaded plugin manifests (id, name, capabilities, `config_fields`) |
| `GET` | `/providers` | list configured `ProviderConnection`s (no credentials in the response) |
| `POST` | `/providers` | validate config against the catalog, test-connect through the plugin, encrypt, persist |
| `PATCH` | `/providers/{uid}` | enable/disable, rename, toggle `import_existing`/`mirror_structure` |
| `DELETE` | `/providers/{uid}` | soft-delete |
| `GET` | `/providers/{uid}/objects?parent_ref=` | admin-only browse of the connection's physical StorageObject index (from SQLite, never a live provider call); paginated — see "Pagination" below |
| `GET` | `/providers/{uid}/sync` | admin-only; `{status: "idle"\|"running", connection_id}` — whether an explicit background sync is in flight (Storage UI polls this) |
| `POST` | `/providers/{uid}/sync` | admin-only; **202 Accepted** — schedules background reconcile (plugin list → upsert StorageObjects → MediaFiles; remote-newer snapshots prior index into MediaFile `history`; ours-newer with `mirror_structure` pushes content back) |

| `POST` | `/providers/{uid}/oauth/start` | Google-Drive-style OAuth connect flow (rclone plugin only, for now) |
| `GET` | `/providers/{uid}/oauth/callback` | completes the OAuth flow, stores the resulting token in the encrypted config |

`POST /providers` / `PATCH /providers/{uid}` accept `import_existing` /
`mirror_structure`. Enabling `mirror_structure` is rejected (422) when the
provider manifest lacks the `move` capability (Telegram and other flat
providers).

### Files (user library — replaces `/resources`)

The dual-layer model's user-facing surface (`docs/11-dual-layer-library.md`).
Listing reads SQLite only — it never waits on provider I/O or sync.

| Method | Path | Notes |
|---|---|---|
| `GET` | `/files?parent_id=` | browse the library tree; paginated — see "Pagination" below |
| `GET` | `/files?scope=owned\|shared_with_me\|shared_by_me\|all_visible\|starred\|trash` | ACL-filtered browse; `include_deleted=true` with `owned` only |
| `GET` | `/files?scope=trash` | the recycle bin: the caller's own soft-deleted *roots* (a cascaded child stays hidden behind its deleted folder), flat — `parent_id` is ignored; ordered by `deleted_at` desc. Each item carries `deleted_at` |
| `POST` | `/files` | upload (multipart: `provider_connection_id`, `name`, `file`, optional `parent_id`) or make a library folder (`type=folder`, no provider involved) |
| `GET` | `/files/{id}` | metadata (incl. size/content-type derived from the linked primary StorageObject) |
| `HEAD` | `/files/{id}` | metadata-only headers (`Content-Type`, `Content-Length`, `Last-Modified`) |
| `PATCH` | `/files/{id}` | rename/move (`parent_id: null` = move to root) and/or `public_permission`; mirrored to the provider only per the connection's `mirror_structure` + plugin capability |
| `GET` | `/files/{id}/content` | streaming read via MediaFile → primary StorageObject → plugin; `Range` supported |
| `GET` | `/files/{id}/permissions` | list per-user ACL entries |
| `PUT` | `/files/{id}/permissions` | grant/replace one user's level (`0`/`"none"` revokes) |
| `POST` | `/files/{id}/link` | attach an indexed-but-unlinked StorageObject as the file's `primary` content |
| `POST` | `/files/{id}/temporary-link` | mint a temporary signed URL (MANAGE, same gate as sharing), HMAC'd with the caller's own access key (`user_access_keys`, doc 04). Body `{"expires_in": seconds}` (default 3600, min 60, max 604800); returns `{url, key_id, expires, expires_at}` — see "Direct-link alias" below |
| `DELETE` | `/files/{id}` | soft-delete (two-step; `?permanent=true` removes the library row — StorageObject and remote bytes always survive, doc 11 v1 rule) |
| `POST` | `/files/{id}/restore` | undo a soft-delete |

#### Trash retention

Soft-deleted items sit in `scope=trash` for **30 days**, counted from
`deleted_at`. A nightly job (APScheduler cron, 00:00 Asia/Tehran, wired in
`server/server.py`'s lifespan → `apps/media_files/worker.py`) permanently
purges every trash root older than that, subtree included — same rules as
`?permanent=true`: only library rows go; StorageObjects and provider bytes
survive. Restore before the window closes and nothing is lost.

### Pagination

The list endpoints — `GET /files` (browse and `?q=` search) and
`GET /providers/{uid}/objects` (browse and `?q=` search) — accept:

| Query param | Default | Bounds |
|---|---|---|
| `limit` | `50` | min `1`, max `200` |
| `offset` | `0` | min `0` |

and return a page envelope instead of a bare array:

```json
{
  "items": [ ... ],
  "total": 1203,
  "limit": 50,
  "offset": 0,
  "has_more": true
}
```

`total` counts the full filtered (or, for search, ranked) result set —
never just the returned slice — so `has_more` is simply
`offset + len(items) < total`. `GET /files` browse lists accept `sort=name`,
`sort=updated_at`, or `sort=type` plus `order=asc|desc`; folders always come
before files. The default order is ascending except for `updated_at`, which
defaults to descending when `order` is omitted. Search keeps its rank order,
and trash keeps newest-deletion-first ordering regardless of these parameters.

### Direct-link alias

The `/f/{uid}` short-link stays as a convenience alias over
`GET /files/{id}/content` — `Range` support and `HEAD` carry over; it is
readable anonymously when the file's `public_permission` allows. It is not
a second, competing files API.

It also serves **temporary signed links** (S3-style — no share_links
table): `GET /f/{uid}?expires=<unix>&key_id=<access_key_id>&sig=<hex>`
where `sig` is HMAC-SHA256 over the canonical string
`"{file_uid}:{expires}:{access_key_id}"` keyed with the minting user's
access-key *secret* (`user_access_keys`, doc 04 — never the installation
master key), compared with `hmac.compare_digest`. `key_id` is the public
half of the pair, so verification can prove which user signed the link.
A valid, unexpired signature grants READ on its own — no
`public_permission` needed — provided (a) `key_id` resolves to a
still-**active** key and (b) that key's owner is still allowed to share
the file (they own it or hold MANAGE via the ACL); either failing looks
exactly like a bad signature (404). Access is the OR of three grants,
checked in this order: permanent public (`public_permission == "read"`),
valid signature, or the authenticated caller's ACL — so junk signed
params never lock out a grant the caller already has. Links are minted
via `POST /files/{id}/temporary-link` (the caller's default key is
created on first use for accounts predating access keys); deactivating a
key revokes every link it ever signed, per-user, without touching the
master key.

### Health

| Method | Path | Notes |
|---|---|---|
| `GET` | `/health` | liveness + DB round-trip |
