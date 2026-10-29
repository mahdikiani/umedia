# WebDAV gateway

UMedia serves your library over WebDAV so Finder, Windows Explorer,
Cyberduck, `rclone` and `davfs2` can browse, upload, download, move, copy
and delete files. Like the S3 gateway it is a protocol adapter over
`MediaFile`: no business rule lives in `apps/webdav`. ACL, placement,
provider mirroring and trash are the media service's.

## Endpoint

`https://<host>/dav/` (the route prefix, not under `/api/v1`).

| Setting | Default | |
|---|---|---|
| `UMEDIA_WEBDAV_ENABLED` | `true` | `false` makes every `/dav` request 404 |
| `UMEDIA_WEBDAV_PREFIX` | `/dav` | Move it when UMedia shares a domain, e.g. `/umedia-dav` |
| `UMEDIA_WEBDAV_MAX_UPLOAD_BYTES` | `2147483648` | PUT size cap (413 above it) |
| `UMEDIA_WEBDAV_LOCK_TIMEOUT_SECONDS` | `3600` | Lock lifetime |

Behind a reverse proxy that *strips* a path prefix, send
`X-Forwarded-Prefix: /outer` (or run uvicorn with `--root-path /outer`) so
the `href`s and the `Destination` check use the URL the client sees.

## Credentials

- **Basic**: username = an access key id (`um_…`), password = its secret.
  These are the same keys the S3 gateway uses (create or revoke them on the
  Access keys page; the secret is shown once). The account login password is
  **never** accepted. Revoking a key cuts WebDAV off at once.
- **Bearer**: a normal UMedia/usso-lite access token, for embedding hosts.
- A missing or wrong credential gets `401` with
  `WWW-Authenticate: Basic realm="UMedia", charset="UTF-8"`. `OPTIONS` is
  answered without credentials (Windows probes first).

Use HTTPS: Basic sends the secret on every request, and Windows refuses
Basic over plain HTTP by default.

Example (rclone):

```bash
rclone config create umedia webdav url https://umedia.example/dav vendor other user um_XXXX pass SECRET
rclone ls umedia:
```

## What you see

The root is **your own** library root (`scope=owned`); folders are library
folders, files are `MediaFile`s. Files other people shared with you do not
appear in v1. Names are unique per folder in WebDAV but not in the library,
so a later duplicate is shown as `name (2).ext` — the same rule as the S3
gateway. Dot names (`.DS_Store`) are kept as written. A path segment of `.`,
`..`, or one containing `/`, `\` or NUL is a 400.

## Verbs

| Verb | Behaviour |
|---|---|
| `OPTIONS` | `DAV: 1, 2`, `Allow`, `MS-Author-Via: DAV` |
| `PROPFIND` | Depth `0`/`1` (missing = `1`). `infinity` → 403 `propfind-finite-depth`. `allprop`, `propname`, named props (unknown ones → 404 propstat). Live props: `resourcetype`, `displayname`, `getcontentlength`, `getcontenttype`, `getlastmodified` (RFC 1123), `creationdate` (ISO 8601), `getetag`, `supportedlock`, `lockdiscovery` |
| `GET` / `HEAD` | Streamed from the provider; single `Range` (`a-b`, `a-`, `-n`) → 206, bad range → 416; `If-None-Match` → 304. A collection → 405 |
| `PUT` | Body streamed to disk (not held in memory) with the size cap, then written through `MediaFileService.upload` / `replace_content`. 201 new, 204 replaced. 409 missing parent, 405 on a collection, 412 on `If-Match`/`If-None-Match`, 413 over the cap |
| `MKCOL` | 201; 405 exists; 409 no parent; 415 with a body |
| `DELETE` | **Soft delete** → trash (`MediaFileService.soft_delete`), restorable from the UI. 204 |
| `MOVE` | `MediaFileService.update` (rename + re-parent, mirrors to the provider when the connection does). 201, or 204 when it replaced something |
| `COPY` | File: shares the `StorageObject` (same connection) or byte-copies (cross-connection), as in transfers. Folder: recursive, or just the folder with `Depth: 0` (capped at 5000 items → 507) |
| `PROPPATCH` | Stub: dead properties → 200 (accepted, **not stored**), `DAV:` live properties → 403 |
| `LOCK` / `UNLOCK` | Exclusive write locks, in memory, per user and path; refresh with an empty LOCK + `If`. Writes on a locked path without its token → 423 |

`MOVE`/`COPY`: `Destination` must be on the same host and under the mount
(else 502); same source and destination, or into itself → 403; missing
destination parent → 409; `Overwrite: F` with an existing destination → 412
(default `T` trashes the old one first). Moving into a folder that lives on a
different storage connection is refused (403), same as the UI.

## Known limits

- The plugin protocol takes whole `bytes`, so after the body is staged on
  disk a PUT is read into memory once to hand it to the plugin (the tus path
  does the same). That is why the default cap is 2 GiB, not unlimited.
- Locks live in the process: they protect one user's clients from each
  other on one replica, not across replicas, and are lost on restart.
- No dead-property storage, no `quota-*` properties, no shared-with-me tree.

## Code map

`apps/webdav/` — `routes.py` (one catch-all route + error mapping),
`auth.py`, `service.py` (verbs), `tree.py` (path ↔ library node),
`xml.py` (PROPFIND/LOCK parsing, 207 bodies), `locks.py`, `paths.py`.
Tests: `tests/test_webdav.py` (every verb over httpx/ASGI) and
`tests/test_webdav_rclone.py` (live rclone, skipped without the binary).
