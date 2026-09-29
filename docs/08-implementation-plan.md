# Implementation Plan: apps/media becomes the Universal Data Layer backend

This is the durable "why" behind the current build. Read this before touching
`apps/api`, `apps/media`, or the provider/plugin code. Task-level tracking
lives in `docs/09-tasks.md`; this file explains the decisions behind those
tasks so an interrupted session (human or agent) can pick the work back up
without re-deriving them.

## How we got here

1. Original ask: make the existing `apps/api` skeleton multi-provider,
   multi-instance, and "plugin-pluggable, like WordPress but secure."
2. `apps/api` was rejected outright: it was scaffolded on an unexplained
   `rclone`-base-image assumption, only 2 of its 9 cataloged providers
   (`local`, `telegram`) had real adapter code, and the author isn't
   satisfied with it. **`apps/api` is deleted, not refactored.**
3. `apps/media` — a separate app the author already architected for a
   related "unified file manager" purpose, and is happy with
   architecturally — becomes UMedia's backend instead, restructured in
   place. `apps/web` calls it going forward.
4. Framing correction, quoted directly because it drives every naming
   decision below:

   > "This is not a filesystem abstraction. This is a resource abstraction
   > layer where filesystem is only one possible provider."

   This is expanded in `docs/00-product-vision.md` through
   `docs/07-agent-instructions.md` (the **Universal Data Layer** docs),
   which are the primary source of truth from here on. The older ALLCAPS
   docs (`DOMAIN_MODEL.md`, `API_DESIGN.md`, `PRD.md`, etc.) are marked
   superseded but kept — they still hold detail (e.g. the pasted Persian PRD's
   HATEOAS/security requirements) worth mining during implementation.

## The reframing, concretely

- **Entity is `Resource`**, not `MediaFile`. Fields per
  `docs/04-data-model.md`: `id`, `provider_connection_id`, `type` (`file`,
  `folder`, `object`, `document`, `message`, …), `name`, `parent_id`
  (optional — folders are optional), `metadata` (provider-specific bag),
  `content_reference` (opaque-to-core pointer to fetch bytes).
- **Provider interface verbs are resource-generic**: `connect()`,
  `disconnect()`, `status()`, `list()`, `get()`, `create()`, `update()`,
  `delete()`, plus a content-streaming operation. This replaces the
  file-transfer-flavored `StorageBackend` method names
  (`upload_file`/`download_file`/…) as the **plugin contract's vocabulary**
  — `apps/media`'s existing `local_storage.py`/`s3_storage.py`/
  `nextcloud_storage.py` bodies remain the real implementations underneath;
  only the boundary verb names change.
- **API is resource-centric**: base `/api/v1`; `GET/POST /providers`;
  `GET /resources` (browse, `parent_id` filter); `GET /resources/{id}`;
  `GET /resources/{id}/content` (stream). `apps/media`'s working
  `/f/{uid}/{path}` short-link (Range support, presigned-URL redirect,
  `HEAD`) is kept as a convenience alias on top of `/resources/{id}/content`
  — it already satisfies the "direct access via `/f/{uid}`" requirement from
  the earlier pasted PRD.
- **Provider set for this pass**, per `docs/06-roadmap.md`'s Phase 1: local
  filesystem, S3, Google Drive, Telegram. Google Drive ships through a
  **generic `rclone` plugin** (rclone has a first-class `drive` backend)
  rather than a bespoke Google API client, which also means
  onedrive/dropbox/webdav/nextcloud/ftp/sftp come along mechanically once
  that plugin exists — only `google_drive` is a committed, tested Phase-1
  deliverable. Google Drive's OAuth requirement means the core needs a
  small connect-flow addition (OAuth start/callback per connection) beyond
  static config fields.
- **Explicit backlog** (see `docs/09-tasks.md`), per `docs/06-roadmap.md`'s
  own phase split: Phase 2 — resource indexer, full-text search, AI
  embeddings ("Index != Storage" still holds, per the older
  `MVP_SCOPE.md`). Phase 3 — a **WebDAV server** exposing the unified
  resource tree for OS-level mounting (distinct from the WebDAV *provider
  client* that talks to a WebDAV backend), AI agent access, smart
  organization. Also backlog: viewer plugins, dual-pane/tree GUI, rebasing
  `apps/web` on `next-shadcn-admin-dashboard`.

`docs/07-agent-instructions.md`'s rules are adopted directly: don't couple
core to providers, every external service is a provider plugin, keep
interfaces stable, write contract tests per provider, explain
architecture/tradeoffs before large changes.

## apps/media reuse

`apps/media` is real, working code, just file-shaped and Mongo-backed.
`server/db.py` currently points Beanie at
`mongomock_motor.AsyncMongoMockClient` — an in-memory mock, so there is no
real persisted data to migrate; the SQLite swap is a clean rewrite.

| Reuse from `apps/media` | Becomes |
|---|---|
| `storage_backend/local_storage.py` | Body of the `local` plugin |
| `s3_storage.py` (aioboto3, multipart), `nextcloud_storage.py` (WebDAV) | Reference only — S3 and Nextcloud/WebDAV now go through the `rclone` plugin instead of a bespoke async client, see `docs/03-provider-system.md` |
| `StorageBackend` ABC + subclass registry | The internal shape a plugin implements behind the new resource-verb HTTP contract |
| Content-addressed dedup, history/versioning, two-step soft/hard delete+restore | Kept as `Resource` fields/service behavior |
| Streaming reads w/ `Range`, `/f/{uid}/{path}`, `HEAD`, presigned-URL redirect | Kept, wired as the alias described above |
| Base64 upload, remote-URL upload (blocking + background), icon/preview-by-mime | Kept |
| `EncryptionService` (AES-CTR) | Kept, available for at-rest encryption |
| `apscheduler` background cleanup jobs | Kept (pattern also already used in `apps/api`) |
| `FileValidator`/`FileHashCalculator` | Kept |

Required changes to `apps/media` beyond renaming:

1. **DB**: Beanie/Mongo(-mock) → SQLAlchemy + SQLite via
   `fastapi_mongo_base.sql.models.BaseEntity` (the foundation `apps/api`
   already proved out). SQL mode has no generic CRUD router (unlike Mongo's
   `usso_routes.AbstractTenantUSSORouter`, which `apps/media` currently
   leans on) — routes are hand-written Route → Service → Repository → Model
   per `docs/07-agent-instructions.md` / `docs/AGENT_RULES.md`.
2. **Storage routing**: today one global backend via
   `Settings.STORAGE_BACKEND` (singleton). Becomes connection-routed: every
   `Resource` carries `provider_connection_id`; the plugin registry
   resolves which running plugin process to call from that connection's
   `provider_type`. This is what delivers multi-provider +
   multiple-instances-per-provider.
3. **Isolation**: in-process `StorageBackend` subclasses → subprocess +
   Unix-socket REST plugins.
4. **Auth**: full external USSO + multi-tenant fields (`workspace_id`,
   `tenant_id`, `x-api-key` impersonation, per-user ACL list) →
   `usso.lite` (`~/Projects/pkgs/usso`, uncommitted local work — path
   dependency for now; pin a real release once it's published), single
   bootstrap admin, no multi-user/workspace model, matching
   `docs/MVP_SCOPE.md`.
5. **`apps/api` deleted** entirely, after porting what's worth keeping:
   `ProviderConnection` model shape, `utils/crypto.py`, the Alembic setup
   pattern, the `telegram.py` adapter body.
6. **`apps/web`** points at `apps/media`'s new `/api/v1` base (superseding
   `apps/media`'s current `/api/media/v1`, now that it's the whole backend).

## Plugin architecture

```
apps/media (FastAPI core)
  │  on startup: for every *enabled* plugin manifest → spawn + supervise
  ▼
PluginProcessManager
  │  HTTP over unix:///run/umedia/plugins/<plugin_id>.sock
  ▼
[local-plugin]  [telegram-plugin]  [rclone-plugin]
```

- **Manifest** (`plugin.json` per plugin dir): `id`, `name`, `description`,
  `entrypoint`, `capabilities`, `config_fields` (same shape as `apps/api`'s
  `ConfigField`), `enabled`. Replaces `providers/catalog.py`.
- **Provider plugin REST contract**, resource-verb shaped:
  `POST /connect` (≈ `test_connection`), `GET /resources?parent_id=`
  (≈ `list`), `GET /resources/{id}` (≈ `get`/`stat`),
  `GET /resources/{id}/content` (streaming read),
  `POST /resources` (≈ `create` — upload or make-folder),
  `PUT /resources/{id}` (≈ `update` — move/rename/overwrite),
  `DELETE /resources/{id}`, `GET /status`. Every call carries the
  already-decrypted connection config the core attaches per-call; a plugin
  never persists secrets or touches the core's SQLite file directly.
- **Process manager**: spawn enabled plugins at startup, `GET /health`
  checks, restart-with-backoff on crash, clean shutdown in the FastAPI
  lifespan. Evaluate `circus` before hand-rolling this — see
  `docs/02-architecture.md` "Tooling reuse."
- **Plugins built this pass** — reconsidered to lean on `rclone` more than
  originally scoped, since it already solves "many remote types, one
  interface" well; see `docs/03-provider-system.md` for the full reasoning:
  - `local` (ports `local_storage.py`) — kept native, no network/auth
    surface.
  - `rclone` (generic, runs `rclone rcd` internally rather than shelling
    out per call — rc API for metadata, `rclone cat`/`rcat` subprocess
    streaming for content) — covers **`s3`** (the standalone `s3` plugin
    from the earlier draft is dropped; `apps/media`'s `s3_storage.py` is
    kept as reference only) and `google_drive` for Phase 1, mechanically
    supports onedrive/dropbox/webdav/nextcloud/ftp/sftp once it exists.
    Google Drive's OAuth: check rclone rcd's own `config/create` OAuth
    support before hand-building a start/callback flow.
  - `telegram` (ports `apps/api/providers/telegram.py`, Kurigram) — kept
    native, rclone has no Telegram backend.

## Development process: TDD, every phase

Testing is not a separate late phase — each phase in `docs/09-tasks.md`
ships red → green → refactor:

1. Write the failing test(s) first (unit test for a repository/service
   method, a contract test for a plugin endpoint, an integration test for a
   route).
2. Implement the minimum to pass.
3. Refactor with the test as a safety net.
4. Only then check the task off in `docs/09-tasks.md` — a task is not done
   until its test exists and passes.

The plugin REST contract (`connect`/`resources`/`content`/…) gets a
**shared contract test suite** written before the first plugin (`local`) is
implemented, then run unchanged against every subsequent plugin — this is
"keep interfaces stable" in practice. Same for the `Resource` service
(dedup, versioning, soft-delete): tests first, against a fake/in-memory
repository, before the SQLAlchemy repository exists.

## Verification

- `cd apps/media/app && uv run pytest` (or the project's test runner) and
  lint clean.
- Manual smoke: single `docker compose up --build` boots one container;
  health endpoint OK; auth bootstrap via `usso.lite` sets the cookie-session
  shape `apps/web` expects; creating a `local` provider connection
  round-trips through the new plugin subprocess (proves socket + contract
  end-to-end); uploading a resource, then fetching it via `/f/{uid}` with a
  `Range` header, still streams correctly; `apps/web` has no dead calls to
  the deleted `apps/api`.
- `docs/09-tasks.md` reflects true state at the end of every session — no
  task checked that isn't backed by a passing test or a manual smoke check
  above.
