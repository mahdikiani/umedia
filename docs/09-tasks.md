# Tasks

Resumable checklist for the rebuild described in
`docs/08-implementation-plan.md`. Check a box only once its test is green
(see that doc's "Development process: TDD" section) or, for non-code items,
once the artifact actually exists. If you are an agent picking this session
back up, read `docs/08-implementation-plan.md` first, then resume at the
first unchecked box below, in order.

Live, click-through verification of what this file claims is done lives
separately in **`docs/10-qa-handoff.md`** — written for an independent
testing agent to work through against the live preview, with its findings
relayed back here.

## Phase 0 — Docs

- [x] P0.1: `docs/08-implementation-plan.md` written
- [x] P0.2: `docs/09-tasks.md` written (this file)
- [x] P0.3: `docs/02-architecture.md` updated with concrete decisions (SQLite, single-container, subprocess+socket plugin isolation)
- [x] P0.4: `docs/03-provider-system.md` updated with the exact provider plugin REST contract
- [x] P0.5: `docs/04-data-model.md` updated with exact `Resource`/`ProviderConnection` field lists
- [x] P0.6: `docs/05-api-design.md` updated with the full route list (`/providers`, `/resources`, `/resources/{id}`, `/resources/{id}/content`, `/f/{uid}` alias, auth routes)
- [x] P0.7: Superseded ALLCAPS docs (`DOMAIN_MODEL.md`, `API_DESIGN.md`, `PROVIDER_ARCHITECTURE.md`, `ADDING_A_PROVIDER.md`, `PRD.md`, `PROVIDER_SUPPORT.md`, `TECH_DESIGN.md`, `MVP_SCOPE.md`, `IMPLEMENTATION_PLAN.md`, `AUTHENTICATION_DESIGN.md`, `DATABASE_DESIGN.md`, `FRONTEND_DESIGN.md`, `NFR.md`, `REQUIREMENTS.md`, `SYNCHRONIZATION_DESIGN.md`, `TESTING_STRATEGY.md`) marked with a one-line pointer to the `00`–`09` series
- [x] P0.8: Root `README.md` rewritten for UMedia
- [x] P0.9: Root `LICENSE` added (MIT)
- [x] P0.10: Root `CONTRIBUTING.md` added, pointing at `docs/07-agent-instructions.md`

## Phase 1 — Foundation swap in `apps/media` ✅ done

- [x] P1.1: Tests for `usso.lite` cookie-wrapper auth flows (setup/login/logout/password-change) written first (`tests/test_auth_service.py`, `tests/test_app_routes.py`)
- [x] P1.2: `apps/media` depends on `usso[lite,fastapi]` (pinned PyPI release `0.32.9`)
- [x] P1.3: `usso.lite` mounted; thin cookie-issuing wrapper implemented (`apps/auth/`: `/auth/setup`, `/auth/sessions`, `/auth/sessions/current`, `/auth/password`, httponly `usso-access-token`/`usso-refresh-token` cookies)
- [x] P1.4: `jwt_guard`-equivalent middleware rewritten against `usso.lite` verification (`apps/auth/middleware.py`, reuses `usso.lite.dependency.resolve_current_user` directly rather than re-implementing token/session checks)
- [x] P1.5: Tests for the ported `ProviderConnection` repository/service written first (`tests/test_provider_repository.py` new real-SQLite integration tests; `tests/test_provider_service.py`/`test_provider_catalog.py`/`test_provider_adapters.py`/`test_crypto.py` adapted from `apps/api`)
- [x] P1.6: `beanie`/`mongomock_motor`/`pymongo` dropped; SQLAlchemy + SQLite wired via `fastapi_mongo_base.sql.models.BaseEntity` (`server/database.py`), with `PRAGMA journal_mode=WAL` + `busy_timeout` set at connection setup (see `docs/02-architecture.md` "Correctness & consistency")
- [x] P1.7: `ProviderConnection` model/repository/service/routes ported from `apps/api` (`apps/provider_connections/`, `providers/`)
- [x] P1.8: `utils/crypto.py` (credential encryption) ported from `apps/api`
- [x] P1.9: Alembic set up in `apps/media` (pattern ported from `apps/api`; single `0001_provider_connections` migration so far, verified with a real `alembic upgrade head` run; `docker-entrypoint.sh` now runs it before `main.py` starts)

`apps/files` (the old Beanie-based browsing/upload implementation) is
deliberately unmounted and its obsolete tests are skipped by
`conftest.py`. The same is true of the retired `/resources` HTTP surface;
the MediaFile/StorageObject implementation is mounted instead. Coverage
therefore excludes only those superseded modules, plus the unused legacy
image helper and worker, while still running and measuring the retained
`apps/resources` service/repository tests. The CI gate is 80% for the
maintained backend code; upload lifecycle tests exercise the active MediaFile
tus route.

## Phase 2 — Plugin runtime ✅ done

- [x] P2.0: Evaluated `circus` vs hand-rolled — **hand-rolled**, decision +
      reasoning recorded in `docs/02-architecture.md` "Tooling reuse, not
      reinvention" (`circus` hard-requires `pyzmq`/`tornado`, a second
      event loop; not worth it for our modest, DB-driven, library-called
      requirement)
- [x] P2.1: Process-manager tests written first (`tests/test_plugin_process_manager.py`, against `tests/fixtures/fake_plugin.py`) — spawn, health-check, crash-restart-with-backoff, graceful/forced stop, double-start rejection, never-becomes-healthy timeout
- [x] P2.2: `plugins/manifest.py` (`PluginManifest`/`ConfigField` Pydantic schema, `tests/test_plugin_manifest.py`)
- [x] P2.3: `plugins/process_manager.py` (`PluginProcessManager`: spawn/health/restart-with-backoff/graceful shutdown over Unix sockets; also drains child stdout/stderr into the logger — an unread pipe would otherwise block a chatty plugin's next `write()`, see `docs/02-architecture.md` "Correctness & consistency" #1)
- [x] P2.4: `plugins/client.py` (`PluginClient`, `httpx`'s built-in UDS transport + typed contract models from `plugins/contracts.py`); explicit timeouts everywhere; `list`/`get`/content-read retry-with-backoff on timeout, `create`/`update`/`delete` never auto-retry
- [x] P2.5: `plugins/registry.py` (`PluginRegistry`: loads manifests from `<plugins_dir>/<plugin>/plugin.json` or `.../manifests/<remote_type>.json`, groups by shared `process_key` for multi-remote-type plugins like `rclone`). **`apps/provider_connections` is now wired to it** (done as a follow-up after Phase 3, once real plugin directories existed to point at — see P3.5 below): the old `providers/catalog.py`+`providers/factory.py` package is deleted, not just superseded.
- [x] P2.6: `plugins/sdk.py` (`PluginBackend` ABC + `create_plugin_app()` + `run_plugin()` — a plugin author implements 7 methods and gets the full REST contract, error-status mapping via `PluginBackendError`/`ConnectionFailedError`/`ResourceNotFoundError`, and `/health`+`/status` for free)
- [x] P2.7: Shared contract suite (`tests/plugin_contract.py`'s `run_contract_suite()`), proven end-to-end against a real Unix socket via `tests/fixtures/reference_plugin.py` (in-memory, built on the SDK) in `tests/test_plugin_contract_reference.py` — Phase 3's real plugins get tested by running this exact function against their own backend

Also fixed while building this (see `docs/02-architecture.md`
"Correctness & consistency" #5): a pytest-asyncio event-loop-scope
mismatch (session-scoped fixtures vs. function-scoped tests) that broke
teardown for any fixture holding real subprocess transports — worth
knowing before writing Phase 3's plugin tests, not just this phase's.

**Retroactive fix, found while building Phase 3's `local` plugin**:
`PluginProcessManager.start()` spawned with no explicit `env=`, so
plugins inherited the core's *entire* environment — including
`DATABASE_URL` and `UMEDIA_MASTER_KEY` — silently contradicting the
isolation model this phase's docs already claimed. Fixed to an
allowlist (`env=` param, bare `PATH` otherwise); regression-tested in
`test_plugin_process_manager.py`.

## Phase 3 — Provider plugins ✅ done (2 follow-ups noted, tracked in Backlog)

- [x] P3.1: `plugins/local/` — contract suite red → port `local_storage.py` body → green. Root-path containment ported from `providers/local.py`'s safety check (`UMEDIA_LOCAL_STORAGE_ROOT` env var, passed explicitly via `PluginProcessManager.start(env=...)` — see the env-isolation fix below); real filesystem CRUD, `Range` reads, folders. Found & fixed a real contract bug while wiring this up: `tests/plugin_contract.py` assumed a resource's `id` never changes across `update()`, true for opaque-id providers (Drive/S3) but false for a path-addressed one (`local` — the path *is* the id). Fixed the suite to always use the latest response's `id`; documented as a hard rule in `docs/03-provider-system.md`.
- [x] P3.2: `plugins/rclone/` — `rclone rcd`-backed adapter, generic across remote types: rc API (`operations/list`/`stat`/`mkdir`/`deletefile`/`purge`/`movefile`) for metadata, `rclone cat`/`rcat` subprocesses for streamed content, config translated into an rclone inline connection-string remote (`:type,k=v,...:path`) built fresh per call — **never written to rclone's on-disk config**. Verified against a real `rclone` binary before writing any code (`rc` JSON shapes, `cat --offset/--count` range reads, connection-string quoting rules — an unquoted `endpoint=http://host:port` breaks, since `:` is also the remote-spec delimiter). Full contract suite passes end-to-end against a real spawned `rclone rcd` subprocess (`tests/test_plugin_rclone_contract.py`, via a permanent-but-uncataloged `rclone_local_debug` remote type kept specifically so this doesn't need real cloud credentials). `s3`/`google_drive` connection-string builders are unit-tested directly (`tests/test_plugin_rclone_backend.py`) and were hand-verified against a moto-mocked S3 server for `list`/`stat`/`mkdir`/read — moto's own AWS-SDK-v2 incompatibility (a known moto limitation, reproduces even for a plain `rclone copyto` of a real file) blocked verifying the *write* path against S3 specifically in this session; the write mechanism itself (`rcat` streaming) is proven end-to-end via the `local` backend. Follow-up: verify `s3` writes against real AWS/MinIO or a moto version without this gap.
- [x] P3.3 (investigation): Checked rclone rcd's own OAuth support (`config/create --non-interactive`, `rclone authorize`) — **doesn't fit**, both assume a local config file + browser on rclone's own host, backwards from our headless-server/remote-admin-browser topology, and would write to disk. Conclusion + the concrete plan (core hand-builds a standard OAuth2 flow, stores the result in rclone's documented `token` JSON-blob shape, `rclone` plugin needs zero changes) recorded in `docs/03-provider-system.md`.
- [x] P3.3 (implementation): Google Drive OAuth via **localhost-redirect paste flow** — `POST /providers/oauth/start` + `POST /providers/oauth/complete` (not a server-received callback). UI shows the authorize URL; user pastes redirect URL / code / token JSON; core exchanges and stores rclone's `token` JSON blob + client_id/secret. In-memory CSRF state on `app.state` (single-container).
- [x] P3.4: `plugins/telegram/` — resources are documents in an admin-owned channel; `connect()` ports `apps/api/providers/telegram.py`'s session/permission check verbatim, extended to full CRUD (`iter_messages`/`get_messages`/`send_file`/`iter_download`/`delete_messages`). **Not live-verified** (no real Telegram session/channel possible in this environment) — tested against a hand-written fake `TelegramClient` (`tests/test_plugin_telegram_backend.py`, 11 tests) that verifies UMedia's own glue logic (right calls, right args, right `Resource` mapping), plus a real-subprocess boot check (`test_plugin_telegram_boots.py`) proving the process/manifest/SDK wiring itself works. Two platform limitations are load-bearing, not bugs: channels are flat (no folders — `create(type=folder)` is rejected) and there's no in-place filename rename (a pure rename re-uploads under the new name, so `update()`'s `id` always changes — allowed by the id-stability rule from P3.1). **Follow-up**: verify against a real bot/channel once credentials are available.
- [x] P3.5 (closed a real gap, prompted by "shouldn't I test something?"): `apps/provider_connections` was still calling the old in-process `providers.factory.create_provider`/`providers.catalog.PROVIDER_CATALOG` after Phase 3 shipped — the new subprocess-isolated plugins existed and were fully tested, but were **not reachable through the live app**. Fixed: `services.py` now takes a `registry` (→ `plugins/registry.py`) and a `Connector` that calls the plugin's real `POST /connect` over its socket (`build_plugin_connector`, injecting the manifest's `remote_type` for `rclone`-backed types); `routes.py`'s `/provider-types` now reads the plugin registry instead of the dict. `server/server.py`'s lifespan spawns every enabled plugin's process at startup (one failed plugin — e.g. a missing `rclone` binary — is logged and skipped, not fatal to the app) and stops them at shutdown. The old `providers/` package and its tests are deleted, not left dangling. New end-to-end test (`test_provider_connection_plugin_wiring.py`) creates a `local` connection through the real HTTP app and confirms it round-trips through an actually-spawned plugin subprocess. Also added `rclone` to the Dockerfile's apt install (it was missing — the plugin would have silently failed to start in a real deployment).

  **Second real bug found by actually running the wired-up app, not just pytest**: `plugin_socket_dir` defaulted to nested under `UMEDIA_DATA_DIR`, and Unix domain sockets have a hard ~108-byte OS path limit. `local.sock` fit; `telegram.sock`/`rclone.sock` didn't, and failed as a silent 10-second startup timeout per plugin instead of a clear error — the app would eventually boot with 2 of 3 plugins missing and nothing but a buried traceback explaining why. Fixed the default to a short, fixed `/run/umedia/plugins` (matches what `docs/02-architecture.md` specified from Phase 2 — the nested default was a drift from that, not an intentional change), and added a fast, explicit length check in `PluginProcessManager.start()` so any future occurrence (e.g. an operator overriding the path with something deep) fails immediately with a clear message instead of a mysterious timeout. Regression-tested. Verified live end-to-end afterward: real `uv run uvicorn`, real `alembic upgrade head`, admin setup, `/provider-types` returning all 4 real manifests, creating a `local` connection returning 201 through an actually-spawned subprocess, and `pgrep` confirming all three plugin processes (`local`, `rclone`, `telegram`) running simultaneously.

### Live preview deployment (testing convenience, not Phase 5)

Deployed `apps/media` to **https://umedia.uln.me** for hands-on testing, via
a standalone `compose.media-preview.yml` at the repo root — deliberately
*not* the `umedia`-named project in `compose.yaml` (that name is already
bound to a *different* checkout's live deployment at
`~/Projects/ai/umedia`, currently serving `drive.uln.me`; reusing it here
risked disrupting those already-running containers). This preview is its
own compose project (`umedia-media-preview`), own volume, own Traefik
router — `apps/api` is untouched and still exists.

Two real build/deploy issues found and fixed getting this up:
- `usso[lite]` wasn't on PyPI at first; a locally-built wheel was vendored
  (`apps/media/app/vendor/usso-0.32.0-py3-none-any.whl`) until `0.32.9`
  shipped — dependency is now a pinned PyPI release (`usso[fastapi,lite]==0.32.9`).
- The Dockerfile runs as a non-root `user` (unlike `apps/api`'s Dockerfile,
  which runs as root), but never created `/data` or `/run/umedia/plugins`
  with that ownership — a named volume mounted at a path the image never
  created comes up `root:root`, and the app couldn't write its own SQLite
  file or spawn plugin sockets. Fixed with an explicit `mkdir`+`chown` in
  the image, before the volume attaches.

Verified against the real public domain (Cloudflare-proxied `*.uln.me`,
already resolving): container healthy in ~5s (migration + all 3 plugins
started), `/api/v1/auth/setup` → `/provider-types` (all 4 manifests) →
`POST /provider-connections` (`local`, 201) all succeeded over
`https://umedia.uln.me`.

Tear down with `docker compose -f compose.media-preview.yml down -v` when
done (the `-v` also drops the preview's SQLite volume — omit it to keep
data across a restart).

## Phase 4 — Domain rewrite ✅ done (1 follow-up noted, not yet scheduled a phase)

- [x] P4.1: `Resource` service tests written first (dedup, versioning, soft/hard delete, restore, volume stats, **and** the processing→completed/failed lifecycle incl. post-write verification and a simulated plugin crash/timeout mid-write) against a fake repository (`tests/test_resource_service.py`, 16 tests). Two real bugs found while writing the fakes: `FakeResourceRepository` returned the *same* mutable object on every call instead of a copy, so an old in-hand `ResourceRecord` reference silently mutated later (masking a real update as a no-op in one assertion) — fixed with `copy.deepcopy()` on every read/write, matching real per-session SQL semantics. `FakePluginGateway.read_content` was written as a coroutine (`async def ... return _iter()`) instead of an async *generator* function (`async def ... yield ...`) — `async for` on the result raised `TypeError: got coroutine`; the correct pattern was then deliberately mirrored in the real `PluginResourceGateway.read_content` (P4.3) with a cross-referencing comment.
- [x] P4.2: `Resource` SQLAlchemy model + repository (per `docs/04-data-model.md`) replacing `models.py`'s Beanie logic (`apps/resources/models.py`, `repository.py`; `resource_metadata` mapped-column workaround for SQLAlchemy's reserved `metadata` attribute; Alembic `0002_resources` migration, applied and schema-verified against a real SQLite file). Real-SQLite integration tests in `tests/test_resource_repository.py` (9 tests), mirroring `test_provider_repository.py`'s pattern.
- [x] P4.3: `FileManager` made connection-aware — `apps/resources/plugin_gateway.py`'s `PluginResourceGateway` resolves a `provider_connection_id` → `ProviderConnection` row → decrypted config + plugin manifest + socket, implementing `PluginGatewayProtocol` fully (`create_resource`/`get_resource`/`update_resource`/`delete_resource`/`read_content`); `apps/resources/factory.py`'s `build_resource_service(request)` wires it together with `ResourceRepository` into a `ResourceService` from `request.app.state`, mirroring `apps/provider_connections/routes.py`'s per-request construction pattern. The `processing` → verify → `completed`/`failed` lifecycle itself lives in `ResourceService` (P4.1); this task was specifically the plugin-resolution glue underneath it. Verified end-to-end in `tests/test_resource_plugin_gateway.py` (2 tests): a real spawned `local` plugin subprocess + a real encrypted `ProviderConnection` row in real SQLite, full create/get/read-content/update/delete round-trip through the actual socket, plus an unknown-connection-id validation-error case. `build_resource_service` itself has no standalone unit test, matching the established pattern for this kind of trivial per-request wiring (`_repository()`/`build_plugin_connector()` in `apps/provider_connections` aren't unit-tested either) — it will be exercised for real once P4.4/P4.5's routes land.
- [x] P4.4+P4.5: Route-level tests written first (`tests/test_resource_routes.py`, 11 tests, against the real HTTP app + a real spawned `local` plugin, mirroring `test_provider_connection_plugin_wiring.py`'s pattern), then `apps/resources/routes.py`/`api_schemas.py` hand-written: `GET/POST /resources`, `GET/HEAD/PUT/DELETE /resources/{id}`, `GET /resources/{id}/content` (streaming, `Range`, `416`), `POST /resources/{id}/restore`, `GET /resources/volume-stats`, plus the `GET/HEAD /f/{uid}` public-link alias (`apps/auth/middleware.py`'s `_is_public`/new `resolve_optional_admin`, gated by the resource's own `public_permission` — `"none"`/`"read"`, set via `PUT /resources/{id}` and a new `ResourceService.set_public_permission`, TDD'd in `test_resource_service.py`). `workspace_id`/`tenant_id`/`x-api-key` impersonation dropped as planned — single-admin only.

  **Two real, previously-latent bugs found by this phase's route-level (real-plugin, real-HTTP) tests — neither was catchable by the fake-repository/fake-gateway unit tests in P4.1, since the fakes never actually interpret `parent_id` or route over a real socket:**
  1. **Wrong `parent_id` sent to the plugin.** `ResourceService.create()`/`update()` passed the app-level `parent_id` (a `Resource.uid`) straight through to the plugin as `CreateResourceIn.parent_id`/`UpdateResourceIn.parent_id` — but a plugin (e.g. `local`) expects *its own* parent id (a path/`content_reference`), an unrelated string. Any upload into a subfolder failed. Fixed with `ResourceService._resolve_plugin_parent_id()`, which looks up the parent `Resource` row and forwards its `content_reference` instead (raising a 422 if the parent doesn't exist or has no `content_reference` yet).
  2. **A `/`-bearing resource id can't be a plain path segment.** Once (1) was fixed, a nested resource's id (`a-folder/child.txt`, from a path-addressed provider) still 404'd on the plugin's own `GET/PUT/DELETE /resources/{resource_id}` — FastAPI's default `{resource_id}` path converter matches exactly one segment, and percent-encoding the `/` as `%2F` doesn't help (verified empirically: ASGI servers decode `%2F` to a literal slash *before* routing, same as if never encoded). Fixed generically at the plugin contract level, not just for `local`: `plugins/contracts.py`'s `encode_resource_id`/`decode_resource_id` (URL-safe base64) are now used by every URL-path resource-id call site in `client.py` (core → plugin) and decoded back in `sdk.py`'s route handlers (plugin side) — transparent to every `PluginBackend` implementation. Regression-covered for *every* plugin at once by extending the shared `tests/plugin_contract.py` suite with a nested-folder-and-child scenario (re-run against `local`, `rclone`, and the in-memory reference plugin — all green) plus a focused `tests/test_plugin_contracts.py` round-trip unit test. This also means `rclone`'s path-shaped ids had the same latent bug since Phase 3; nothing before this phase's tests ever created a resource nested two levels deep through the actual socket.

  **Known gap, since closed (foundational half only) — see below.**

- [x] **Follow-up: provider-connect foundation** — `/provider-connections` → `/providers` renamed (now matches `docs/05-api-design.md`, which already specified this exact shape), `provider_connections.enabled` column added (Alembic `0003_provider_connections_enabled`, applied and schema-verified against a real SQLite file) with `PATCH /providers/{uid}` (rename and/or enable/disable — not a config change, that's still delete + recreate), and a new `connect_flow: "token" | "oauth" | "session"` field on `PluginManifest`, exposed via `GET /provider-types`. `local`/`s3` stay `"token"` (default, unchanged behavior); `google_drive` is now marked `"oauth"`, `telegram` `"session"` — purely descriptive for now, **the oauth/session sub-flow endpoints themselves are not built yet**, every provider type is still created through the one existing `POST /providers` form regardless of this value. `enabled` actually does something, not just decorative: `apps/resources/plugin_gateway.py`'s `_resolve()` — the single choke point every resource operation resolves a connection through — now rejects a disabled connection, checked and regression-tested (`test_disabled_connection_rejects_resource_operations`; note `ResourceService.create()` wraps this, like every other exception from the gateway, into a generic 400 — pre-existing behavior, not something this change altered, confirmed against what an unknown `provider_connection_id` already did before touching anything). Fixed `docs/04-data-model.md`'s `enabled` description too — it previously claimed this drives plugin *process* spawning, which was never actually true (that's manifest-level, since one process commonly serves many connections of the same `provider_type`); now describes what it actually does. 21 new/updated backend tests (repository, service, and full HTTP-round-trip levels) + frontend renamed to match (`lib/api.ts`, `AddStorageForm`, `(dashboard)/layout.tsx`, Files/Settings/onboarding pages) — `tsc`, `eslint`, `vitest`, `next build` all clean.

  **Still open, not part of this pass**: Telegram session (`POST /providers/{uid}/session/start` + `.../session/verify`, phone → code → maybe 2FA, needs new plugin-specific socket endpoints since this doesn't fit the generic resource-verb contract). Google Drive OAuth paste flow is shipped (`POST /providers/oauth/start` + `/complete`).

- [x] **Follow-up: resumable (tus) uploads**, explicit user ask ("frontend shouldn't lock while uploading; send the process to the backend"). `POST /resources`'s old multipart path reads the whole file into memory over one request that must complete fully before the browser hears anything back — no progress, no resuming a dropped connection. Evaluated `tusd` (official Go server, subprocess + Unix-socket proxy + webhook route) vs `tuspyserver` (FastAPI-native router, PyPI); chose `tuspyserver`, pinned exact (`==4.2.13`, no upstream git tags exist, PyPI is the only source of truth) after actually reading its source first — path-traversal guard present and tested, file locking bounded with a best-effort fallback (rewritten after a real reported production outage), chunks streamed straight to disk not buffered in memory. `apps/resources/uploads.py`: `pre_create` hook validates `provider_connection_id`/`name`/`filetype` before any bytes are staged; `upload_complete` hook does **not** await the actual resource creation — it schedules `_finalize()` as a detached background task (tracked in `app.state.tus_finalize_tasks`, awaited with a bounded 10s timeout on shutdown so an in-flight upload isn't cut off mid-plugin-write) and returns immediately, which is what keeps the final PATCH response fast. `_finalize()` reads the staged file and calls the exact same `ResourceService.create()` every other path uses — dedup, `processing`→verify→`completed`/`failed`, all of it — so the frontend needed no new state machine, just to keep polling `GET /resources` (already did). Real bug found writing the resumability test: `tuspyserver`'s own `HEAD` route (used by every tus client to resume) hard-requires a `filetype`/`type` metadata key and 400s without one — undocumented in its README; a plain non-resuming upload works fine without it, so this only surfaces when a real client actually resumes. Fixed by requiring `filetype` in our own `pre_create` validation too, so it fails at creation time with a clear message instead of on some later resume attempt. Tested in `tests/test_resource_uploads.py` (6 tests) against the real HTTP app: full create→PATCH→completion round trip (including reading the result back via `/resources/{id}/content`), a genuine two-PATCH resumability round trip with a `HEAD` offset check in between, and validation-rejection cases — polls `GET /resources` for the background task to settle, same as a real frontend would. Frontend: `tus-js-client` (official) replaces the old `apiForm()` upload call in `app/(dashboard)/files/page.tsx`, with a real per-file progress bar (`components/ui/progress`) during transfer and a "Finalizing…" state (indeterminate progress) while the background task runs, resolved by the same poll-until-settled approach as the tests.

  **Follow-up, same request**: pause/resume/cancel controls, plus a retention window for uploads abandoned mid-transfer (both explicit user asks). Each in-flight upload's `tus.Upload` instance + promise `resolve` is kept in a `useRef` map (`uploadControllers`, not React state — an imperative handle, not renderable data); Pause/Resume call `.abort()`/`.start()` on that instance directly (tus-js-client doesn't fire `onError` for a deliberate abort, so these don't need special-casing there), Cancel calls `.abort(true)` (tus's Termination extension — actually deletes the staged bytes server-side, not just a local pause) and resolves that upload's promise itself so `Promise.all` in `uploadFiles` doesn't hang waiting on a transfer that will never call `onSuccess`/`onError`. Fixed a real bug surfaced while wiring this up: the original "finalizing" row was removed via `.finally()` on the per-upload promise, which fires essentially the same tick as `onSuccess`'s `resolve()` — the "Finalizing…" state was being set and then immediately wiped before it could ever render. Fixed by only clearing `finalizing` rows once `pollUntilSettled()` (already existed) confirms the batch is done, not per-upload the instant its transfer ends.

  Retention: `create_tus_router(days_to_keep=7)` — picked over 24h after the user asked directly (Google Drive uses 7 days for its own resumable-upload sessions in production; losing a large, mostly-finished upload because it was paused over a weekend defeats half the point of using tus). Actually *enforcing* that required writing `apps/resources/uploads.py`'s own `_cleanup_expired_uploads`, not calling `tuspyserver`'s advertised `remove_expired_files()` — that method is documented in the README but **does not exist in the installed source** (confirmed by grepping the package before relying on it, not assumed); the on-disk shape it would have wrapped (`<uid>` data file + `<uid>.info` JSON sidecar with an `expires` field) is simple enough to read directly instead, per `tuspyserver.info`/`params`'s own (undocumented-as-public-API but stable-shaped) format. Runs hourly as a background task from the app lifespan (`run_periodic_cleanup`, cancelled cleanly on shutdown) — frequency is independent of the 7-day retention window, just keeps actual disk usage close to the nominal window rather than lagging a full sweep interval behind it. 7 tests in `tests/test_tus_cleanup.py` (expiry-string parsing for both the RFC 7231 and float-timestamp shapes `TusUploadParams.expires` allows, deletion, non-deletion of not-yet-expired and no-recorded-expiry uploads, and graceful handling of a malformed `.info` file / missing directory without crashing the sweep).

  **Follow-up, user asked directly about upload/download speed**: download was already fine as architected (real end-to-end streaming, `local`/`rclone` backends read in 64KB chunks, nothing ever fully buffered — see P3.1/P3.2). Upload had a real bug: `ResourceService`'s `_hash()` (SHA-256, runs on every create and every content-replacing update, since it's what dedup keys off) was a plain synchronous call -- for a large file this blocks the *entire* shared event loop for however long hashing takes, stalling every other request (other users' reads, plugin health checks) in the process, not just slowing the upload itself. Fixed: `_hash()` now offloads to a thread via `asyncio.to_thread`, both call sites (`create()`/`update()`) updated to `await` it -- no signature/behavior change, purely moves the CPU work off the loop. Structural cost noted but deliberately left alone by explicit user choice: a finalized upload is still read fully into memory once and written to disk twice (staged copy + the plugin's own final write), because content-addressed dedup needs the complete hash *before* deciding whether to write at all. Streaming that away would mean either bounding peak memory only (still two disk writes) or moving to write-first/dedupe-after semantics (fewer writes, but changes what "dedup" means) -- both discussed and explicitly deferred for now as not worth the complexity at present scale.

## Phase 5 — Wiring & cleanup

- [ ] P5.1: `apps/api` deleted entirely
- [ ] P5.2: `apps/web`'s `api()` helper points at `apps/media`'s new `/api/v1` base
- [ ] P5.3: `docker-compose.yml` updated (drop `mongo-net`, single SQLite volume, one container) — also reconcile the root `compose.yaml`'s existing `umedia-files:/storage` volume mount with `server/config.py`'s `local_storage_root` default (`$UMEDIA_DATA_DIR/storage`, i.e. `/data/storage` unless `UMEDIA_LOCAL_STORAGE_ROOT` is set) so the `local` plugin's allowed root actually points at the mounted volume
- [ ] P5.4: `server/worker.py` scheduler jobs updated for the new model names
- [ ] P5.5: Full test suite green + manual smoke checklist (see `docs/08-implementation-plan.md` Verification) passes

## Phase 6 — OSS polish

- [ ] P6.1: Root `README.md` finalized with real run instructions (post-rewrite)
- [ ] P6.2: `CONTRIBUTING.md` finalized

## Completed follow-up — Multi-user ACL foundation

- [x] Auth backend: `admin | user` roles and admin-only `/users` management
- [x] Resource backend: required `owner_id` plus per-user `permissions` ACL
- [x] Settings Users UI in `apps/web` — admin-only Users card (list/create/role/active/delete)

## Phase L — Dual-layer library (StorageObject + MediaFile)

Plan: [`docs/11-dual-layer-library.md`](./11-dual-layer-library.md).
Corrects the single-`resources` model: provider index vs user library.
Naming locked to root `AGENTS.md`: **MediaFile** (user library),
**StorageObject** (physical provider index).

- [x] L0: Docs `04`/`05` updated to match `11-dual-layer-library.md`
- [x] L1: Alembic `0005_storage_media_files` — `storage_objects`, `media_files`, `media_file_objects`, connect flags on `provider_connections`; migrated existing `resources` (uids preserved, `legacy-unassigned` backfilled to first admin; table kept until cleanup)
- [x] L2: Services + import/sync/mirror (TDD — `apps/storage_objects` + `apps/media_files`; tests `test_storage_object_repository` / `test_media_file_service` / `test_media_file_import` / `test_media_file_mirror`)
- [x] L3: `/files/*` + `/f/{uid}` + `/providers/{uid}/objects` + `/providers/{uid}/sync`; web cutover; Add Storage `import_existing` / `mirror_structure` checkboxes; tus finalize → MediaFiles
- [x] L4: Minimal `/storage` provider-objects browse UI
- [x] L5: Preview `0005_storage_media_files` migrate + smoke (`compose.media-preview.yml`)
- [x] L6: Per-user stars (`media_file_stars`, `PATCH` `starred`, `GET /files?scope=starred`) + `/starred` UI
- [x] L7 / Phase A inbound sync: one reconcile (`import_from_provider`) with two triggers — interval poll of enabled connections (`UMEDIA_SYNC_POLL_INTERVAL_SECONDS`, default 900) plus existing `POST /providers/{uid}/sync`; after a complete successful walk, unseen StorageObjects are `status=missing` and linked MediaFiles go to Trash (not hard-deleted); reappear restores the same MediaFile uid. Incomplete listings never mark missing. Uploads from the UI and Infra→Storage push beyond existing `mirror_structure` stay out of scope.

---

## Backlog (explicitly deferred, not dropped)

Per `docs/06-roadmap.md`'s own phase split, plus everything else deferred
during planning:

- [ ] Resource indexer / full-text search / AI embeddings (Phase 2 per
      roadmap; "Index != Storage") — use **SQLite FTS5** for full-text
      (built into SQLite, no extra service — keeps the single-container
      constraint) and **`sqlite-vec`** for embeddings/semantic search
      instead of standing up Elasticsearch/Meilisearch/a vector DB
- [ ] WebDAV **server** exposing the unified resource tree for OS-level
      mounting (Phase 3 per roadmap — distinct from the WebDAV *provider
      client*) — use **[`wsgidav`](https://github.com/mar10/wsgidav)**
      (mature WebDAV protocol implementation, pluggable custom "DAV
      provider" backend) instead of hand-implementing
      PROPFIND/PROPPATCH/LOCK
- [ ] AI agent access surface / smart organization / storage optimization (Phase 3 per roadmap)
- [ ] Viewer plugin contract (pluggable file/document viewers)
- [ ] Dual-pane / tree-structure GUI, temporary drag-drop workspace —
      **backend half done**: `POST/GET /files/transfers` (move/copy bulk
      jobs + progress) and `GET/POST/DELETE /files/temporary` (per-user
      MediaFile pointer clipboard — no storage ops on add; transfers only
      on paste to a real folder); web dual-pane UI still open
- [ ] HATEOAS link expansion beyond the current minimal REST responses
- [x] Rebase `apps/web` on `next-shadcn-admin-dashboard` (https://github.com/arhamkhnz/next-shadcn-admin-dashboard) — adopted its *format*: `shadcn/ui` (`components.json` style `base-nova`, `@base-ui/react` primitives, `--rtl` on since the app is bilingual en/fa), the sidebar-shell dashboard layout (`SidebarProvider`/`AppSidebar`/`SidebarInset` + header), and real routes instead of one monolithic client-state-switched page. **Not** a literal fork of the template repo -- none of its demo pages (crm/kanban/mail/chat/academy/...), zustand preferences store, multi-layout-variant system, or multi-user account-switcher were pulled in; those don't fit a single-admin app. Routes: `/login` (setup/login, was `AuthScreen`), `/` (redirect-only), `/(dashboard)/files` (**new** — the actual Resources API browser: connection switcher, breadcrumb navigation, upload, new folder, rename, delete/restore-eligible soft-delete, share-link toggle via `public_permission`, `Range`-friendly direct-download links), `/(dashboard)/settings` (storage connections + password change, see below), `/onboarding` (see below). `lib/api.ts` adds `apiForm()` alongside the existing JSON `api()` helper, since every `apps.resources.routes` write is `multipart/form-data`, not JSON. Verified: `tsc --noEmit`, `eslint`, `vitest run`, `next build`, and a full `docker build` of the existing `apps/web/Dockerfile` all clean.

  **Follow-up, from the user's first click-through of a live joint preview** (the actual point of standing one up -- this is exactly the kind of gap pytest/tsc/eslint can't see):
  1. **`lib/api.ts`'s error parsing showed `[object Object]`.** apps/media's own errors (`ResourceNotFoundError`, etc.) send a plain string `message`, but usso/fastapi_mongo_base's *built-in* errors (a wrong-password 401, request-validation 422s) send `{en, fa}` -- passing that object straight to `Error(...)` silently stringifies it. Fixed with `extractErrorMessage()`, locale-aware, falling back through `message.en` → any string value in `message` → `detail` → a generic string. Regression-tested through the actual login form (`tests/dashboard.test.tsx`), which also caught two test-only bugs while writing it: no DOM `cleanup()` between tests (vitest.config.ts doesn't set `test.globals`, so `@testing-library/react`'s automatic cleanup hook never registers -- fixed with an explicit `afterEach(cleanup)`), and a `next/navigation` `useRouter` mock returning a fresh object every call, which made `useEffect(..., [router])` refire in a loop and silently exhaust a test's mocked `fetch` queue before the real interaction under test ran (fixed with a stable module-level mock object).
  2. **No onboarding flow.** Landing an admin straight on `/files` right after setup, with zero storage connected, meant either a confusing empty dashboard or a buried "Add storage" button. Added `/onboarding`: `(dashboard)/layout.tsx` now gates the *entire* dashboard shell on "at least one provider connection exists" (not just a one-time post-setup redirect -- it's a real, repeatable gate, so deleting your only connection also bounces you back here), redirecting to `/onboarding` otherwise; `/onboarding` itself redirects to `/files` once a connection exists, and to `/login` if unauthenticated. The provider-picker + connect form was extracted into `components/add-storage-form.tsx` so onboarding and Settings share one implementation.
  3. **Storage management moved out of the sidebar, into Settings** (explicit user call: it's a set-up-once admin concern, not daily navigation like Files) -- the standalone `/storage` route is gone; its content (connection list + "Add storage" dialog) is now a section on `/settings` alongside the password-change form.
- [x] FTP/FTPS, SFTP, and WebDAV (incl. Nextcloud/ownCloud vendors) registered as beta `rclone` remote types. Passwords are rclone-obscured in-process (never in argv); FTP defaults to explicit TLS; SFTP supports password or PEM key and always pins the server host key: the first connect returns 409 `host_key_unknown` with the fingerprint (probed via asyncssh key exchange, no credentials sent), the UI asks "Trust and connect", and a changed key returns 409 `host_key_mismatch` with a destructive-styled warning, like OpenSSH/WinSCP. Verified end-to-end with the shared contract suite against real `rclone serve ftp|sftp|webdav` servers (`tests/test_plugin_rclone_servers.py`). The image now installs pinned rclone 1.75.1 from the official release (Debian ships 1.60.1, which lacks sftp `host_keys`). Live third-party servers not yet tested.
- [x] Connection names are S3 bucket names: validated with AWS bucket rules minus dots (`apps/provider_connections/names.py`), unique per owner, editable inline in Settings. Migration 0015 slugged existing names (old names kept in `meta_data.display_name_before_0015`; downgrade restores them). The S3 API lists `umedia` (whole library) plus one bucket per enabled connection, scoped for list/get/put/delete; PUT into a connection bucket stores on that connection and refuses keys stored elsewhere. Verified live on the preview: 8 buckets, scoped listings, 404 NoSuchBucket for unknown names.
- [x] Provider logos: official marks from `simple-icons@16.34.0` (CC0) for Dropbox, Google Drive, Telegram, Hugging Face, Nextcloud, and S3 vendors detected from the endpoint (`variant`: Cloudflare R2, Backblaze, Wasabi, DigitalOcean, MinIO). OneDrive and AWS have no simple-icons mark (trademark policy), so they use generic glyphs.
- [x] Migration 0016: composite index on `media_file_objects(media_file_id, role, is_deleted)`. Loading the visible library (57,828 files) went from >2 min to ~4 s; it had made S3 listings time out on the preview.
- [x] Hugging Face Buckets (Xet storage) as a native `huggingface` plugin on `huggingface_hub[hf_xet]==2.1.1`. Buckets, not git repos: mutable, no per-write commits, server-side copy by Xet hash (used for move/rename). Empty folders persist via a hidden `.umedia-folder` marker. Tested against an in-memory bucket fake, including the shared contract suite over a real plugin socket; live Hub CRUD remains to be verified with a real token.
- [x] Register OneDrive and Dropbox as beta rclone providers, add their OAuth paste flows, securely retain refresh tokens, and resolve the selected OneDrive drive through Microsoft Graph. Unit and route tests cover the flow; live account authorization and remote CRUD remain to be verified after OAuth credentials are configured in `.env`.
- [x] Publish `usso` (with `lite`) to PyPI and switch `apps/media`'s dependency from a path dependency to a pinned release (`0.32.9`)
- [ ] Verify the `rclone` `s3` remote type's **write path** (`rcat`) against real AWS/MinIO or a moto version without its current AWS-SDK-v2 payload-hash incompatibility — `list`/`stat`/`mkdir`/read are confirmed working against moto now; only the write path is unverified, and only because of that moto gap (see P3.2)
- [ ] Verify the `telegram` plugin against a real bot/channel — everything is currently tested against a hand-written fake `TelegramClient`, not live Telegram (see P3.4); also worth revisiting then: connection pooling instead of reconnecting per call, and whether a lighter-weight "rename" is possible for large files (currently downloads + re-uploads the full content)
- [x] `POST /providers/oauth/start` + `POST /providers/oauth/complete` for Google Drive, OneDrive, and Dropbox (localhost-redirect paste flow; provider-specific OAuth clients are configured by environment variables)
