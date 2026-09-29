# Provider Architecture


A Provider represents an external data source.


Examples:

Storage:

- S3
- Google Drive
- Dropbox


Message based:

- Telegram


Filesystem:

- Local


## Provider Interface


Every provider should implement:


connect()

disconnect()

status()

list()

get()

create()

update()

delete()



## Resource Model


Providers expose Resources.

Resource examples:

S3:

file/object


Google Drive:

file/folder


Telegram:

message/document


## Important


The abstraction must not assume everything is a filesystem.

Folders are optional.

Messages are valid resources.

## Provider plugin: process + REST contract (concrete)

Each provider *type* (`local`, `s3`, `rclone`, `telegram`, …) runs as one
supervised subprocess for the whole core process's lifetime (not one process
per `ProviderConnection` — a single `s3` plugin process serves every S3
connection the user has configured, distinguished per-call by the config the
core attaches). See `docs/02-architecture.md` for why (single-container
constraint) and the isolation guarantees.

The interface above becomes this HTTP contract, served by the plugin over
its Unix socket:

| Verb above | HTTP endpoint | Notes |
|---|---|---|
| `connect()` | `POST /connect` | Validates the given config against the real backend; called on "test connection" and before first use of a newly created `ProviderConnection` |
| `status()` | `GET /status` | Plugin process liveness + last-known backend health; used by the process manager's health check |
| `list()` | `GET /resources?parent_id=` | Returns `Resource` entries (see `docs/04-data-model.md`) directly under `parent_id` (or root if omitted) |
| `get()` | `GET /resources/{id}` | Metadata for one resource |
| content read | `GET /resources/{id}/content` | Streaming body; supports `Range` |
| `create()` | `POST /resources` | Upload content or create a folder (`type: folder` in the body, no content) |
| `update()` | `PUT /resources/{id}` | Rename/move/overwrite |
| `delete()` | `DELETE /resources/{id}` | |

Every request body/query carries the connection's already-decrypted config
(the core attaches it per-call — see `docs/02-architecture.md`). A plugin
never stores it.

**A resource's `id` may change on `update()`.** Providers with opaque,
provider-assigned ids (Drive, S3) can keep the same id across a rename;
path-addressed providers (`local`) cannot — the path *is* the id, so a
rename necessarily changes it. Both are compliant. Callers must always use
the `id` from the most recent response for a resource, never assume an
earlier one still resolves after a write. This is exactly why the core's
`Resource.content_reference` (`docs/04-data-model.md`) is a separate,
plugin-opaque field the core updates after every write, rather than
reusing its own stable `uid` as the provider-facing id — found and fixed
in the shared contract suite (`tests/plugin_contract.py`) while building
the `local` plugin, which would otherwise have required every provider to
fake stable ids across renames.

### Manifest

Each plugin directory carries a `plugin.json`. Example — the `rclone`
plugin registers one manifest entry *per remote type* it fronts, since each
needs its own config-field set and its own catalog card in the UI, even
though one plugin process serves all of them:

```json
{
  "id": "s3",
  "name": "S3 compatible",
  "description": "AWS S3, MinIO, Backblaze B2 and compatible services.",
  "entrypoint": "plugins.rclone.main:app",
  "remote_type": "s3",
  "capabilities": ["list", "read", "write", "delete", "move", "copy"],
  "config_fields": [
    {"key": "endpoint_url", "label": "Endpoint URL", "required": true},
    {"key": "bucket", "label": "Bucket", "required": true},
    {"key": "access_key_id", "label": "Access key", "secret": true},
    {"key": "secret_access_key", "label": "Secret key", "secret": true, "input_type": "password"}
  ],
  "enabled": true
}
```

This replaces the old `apps/api/providers/catalog.py` hardcoded dict — the
core's plugin registry loads every enabled manifest at startup and answers
`GET /providers` from them.

### Plugins shipped this phase

Reconsidered after further thought: **rclone is the right tool for more of
this than originally scoped** — it's a mature, well-tested implementation of
exactly the "many remote types behind one interface" problem UMedia has at
the provider layer, so we lean on it wherever a provider is something rclone
already speaks well, rather than hand-maintaining a bespoke async client.

- `local` — real filesystem, ports `apps/media`'s `local_storage.py`. Kept
  native (not routed through rclone's `local` backend): no network/auth
  surface, already a complete, dependency-free implementation, and it's the
  hot path for the most common case.
- `rclone` — **one generic plugin process for every remaining Phase-1
  provider**, including S3: `s3`, `google_drive` (rclone's `drive` backend),
  `onedrive`, and `dropbox`; the rclone engine can also front
  webdav/nextcloud/ftp/sftp, which remain unshipped.
  `apps/media`'s `s3_storage.py` (aioboto3) and `nextcloud_storage.py`
  bodies are kept as reference/fallback only, not shipped as separate
  plugins — the standalone `s3` plugin from the earlier draft of this doc is
  dropped in favor of routing S3 through here too.

  **Implementation shape**: the plugin process runs `rclone rcd`
  (rclone's remote-control daemon, a persistent process with a JSON-RPC-ish
  HTTP API) internally rather than shelling out a fresh `rclone` invocation
  per call. Metadata operations map onto rclone's `rc` commands directly
  (`operations/list`, `operations/stat`, `operations/deletefile`,
  `operations/movefile`, `operations/copyfile`, `operations/publiclink`);
  content read/write — which the `rc` API isn't a natural fit for — shells
  out to `rclone cat`/`rclone rcat` as a streamed subprocess, piping stdout/
  stdin directly into the plugin's own streaming HTTP response/request body.
  A per-connection rclone remote config (`remote_type` + decrypted
  connection config, translated to rclone's config keys) is generated
  on-the-fly per call, never written to disk.

  **Cloud OAuth — localhost-redirect paste flow (not a server callback).**
  rclone rcd's own OAuth (`config/create`, `rclone authorize`)
  assumes a browser on the same host as rclone and writes to rclone's
  on-disk config — backwards from our headless-server topology, and we
  never write credentials to rclone's config. Instead the core owns a
  provider-specific authorization-code flows that never need the provider
  to hit the UMedia server:

  1. `POST /providers/oauth/start` builds the selected provider's authorize
     URL and stores a
     short-lived CSRF `state` in memory (`app.state.oauth_states`;
     single-container — multi-replica needs a shared store later).
  2. The UI shows the URL (open popup / copy). The user signs in; the provider
     redirects to the configured redirect URI (default `http://localhost`).
  3. The user pastes whatever landed in the browser — full redirect URL,
     query string, bare `code`, or a ready token JSON — into
     `POST /providers/oauth/complete`.
  4. The core validates `state` (skipped for token-JSON pastes), exchanges
     the code at the provider token endpoint when needed, serializes the
     result into rclone's documented `token` JSON blob
     (`access_token`/`token_type`/`refresh_token`/`expiry` RFC3339), and
     creates the `ProviderConnection` with `token` + `client_id` +
     `client_secret`; OneDrive additionally resolves `/me/drive` and stores
     `drive_id`/`drive_type`. The rclone plugin consumes that config as-is
     via the inline connection string.

  Env: `UMEDIA_GOOGLE_OAUTH_CLIENT_ID` / `UMEDIA_GOOGLE_OAUTH_CLIENT_SECRET`
  (aliases `GOOGLE_OAUTH_CLIENT_*`) and optional
  `UMEDIA_GOOGLE_OAUTH_REDIRECT_URI` (default `http://localhost`).
  OneDrive and Dropbox use `UMEDIA_ONEDRIVE_OAUTH_CLIENT_ID` /
  `UMEDIA_ONEDRIVE_OAUTH_CLIENT_SECRET` and
  `UMEDIA_DROPBOX_OAUTH_CLIENT_ID` / `UMEDIA_DROPBOX_OAUTH_CLIENT_SECRET`;
  each has its own optional redirect URI, also defaulting to
  `http://localhost`. The redirect URI must be registered with each app.
- `telegram` — Kurigram, ports `apps/api/providers/telegram.py`. Native:
  rclone has no Telegram backend. The server requires
  `UMEDIA_TELEGRAM_API_ID` and `UMEDIA_TELEGRAM_API_HASH`; without both,
  `/provider-types` reports Telegram as unavailable, the UI locks its card,
  and `POST /providers` rejects attempts to bypass the UI. The API credentials
  are injected server-side and are not shown in the connection form. Login
  runs through `POST /providers/telegram/login/start` with a channel name or
  `@username`, followed by the code and, when enabled, 2FA password endpoints.
  Once signed in, Kurigram resolves the handle or searches joined dialogs for
  an exact title, then verifies channel-admin access. The isolated plugin holds
  the temporary in-memory Kurigram client for up to five minutes. The core
  binds each login id to its authenticated owner, then encrypts and stores the
  exported session and resolved numeric channel id in the new connection. The
  browser never receives the session string; `DELETE` on the login id cancels
  an unfinished attempt.

### Testing

A shared contract test suite (see `docs/09-tasks.md` Phase 2) is written
against this HTTP contract *before* the first plugin is implemented, then
run unchanged against every plugin's actual socket. This is what "keep
interfaces stable" (`docs/07-agent-instructions.md`) means in practice.
