# Architecture

## High Level

                  API Gateway

                       |
                       |

                    Core

        -----------------------------

        Providers        Indexer

        -----------------------------


Provider examples:

- S3
- Google Drive
- Telegram
- WebDAV
- Local filesystem


## Core Responsibilities

Core handles:

- Authentication
- Authorization
- Provider lifecycle
- Resource abstraction
- API


Providers handle:

- Communication with external systems
- Authentication flow
- Data operations


## Plugin Architecture

Providers are isolated modules.

Core must not know provider implementation details.

## Deployment shape (concrete)

One Docker container. No per-provider containers, no Kubernetes. This is a
hard constraint, not a simplification for later: the whole product — core,
every enabled provider plugin, the scheduler, the SQLite file — runs inside
one `docker compose up` service.

Isolation between the core and a provider plugin therefore comes from **OS
processes**, not container boundaries:

- The core spawns each *enabled* plugin as its own subprocess at startup and
  supervises it (health-check, restart-with-backoff on crash, clean shutdown
  in the FastAPI lifespan).
- A plugin process listens on a **local Unix domain socket**
  (`unix:///run/umedia/plugins/<plugin_id>.sock`) and speaks only HTTP over
  that socket. It has no network egress requirement beyond whatever the
  external provider (S3, Telegram, …) itself needs, no access to the core's
  SQLite file, and no access to the master encryption key.
- The core decrypts a `ProviderConnection`'s config and passes it to the
  plugin **per request**; the plugin never persists secrets.
- **No access to the master key or `DATABASE_URL` is enforced, not just
  claimed**: `PluginProcessManager.start()` spawns with an explicit `env=`
  allowlist (bare `PATH` plus whatever the caller passes), never the
  core's full environment. `asyncio.create_subprocess_exec`'s default
  (`env=None`) inherits everything, which would otherwise hand every
  plugin the same environment the core itself reads secrets from —
  caught and fixed while building the `local` plugin (Phase 3), with a
  regression test (`test_plugin_process_manager.py`).

This satisfies "providers are isolated modules; core must not know provider
implementation details" from the section above using the only isolation
primitive available inside a single container.

### Tooling reuse, not reinvention

Per `docs/07-agent-instructions.md` ("prefer simple extensible design") and
explicit direction during planning: don't hand-build what a maintained tool
already does well. For the process-manager piece specifically (Phase 2,
`docs/09-tasks.md`), evaluate before writing custom spawn/health/restart
logic:

- **[`circus`](https://circus.readthedocs.io/)** — a Python process
  supervisor with a programmatic/socket control API (`circusctl`/arbiter)
  designed for *dynamically* adding, removing, and restarting watched
  processes at runtime — a good fit here because which plugins run is
  DB-driven (`ProviderConnection`/manifest `enabled`), not a static list
  known at container build time. Prefer this over hand-rolling
  `process_manager.py`'s spawn/health/restart loop if its control API
  covers what's needed cleanly.
- **`supervisord`** / **`s6-overlay`** — mature single-container
  multi-process supervisors, but configured mainly via static config files;
  weaker fit for runtime-driven start/stop than `circus`'s control API.
- Only fall back to a hand-rolled `asyncio.create_subprocess_exec` loop if
  none of the above cleanly support dynamic, DB-driven process lifecycles.

**Decision (Phase 2, P2.0): hand-rolled, not `circus`.** Checked `circus`'s
actual dependency footprint before deciding: it hard-requires `pyzmq`
(needs the system `libzmq` C library) and `tornado` (its own event loop).
Running it inside this app means either bridging tornado's IOLoop with the
asyncio loop uvicorn/FastAPI already run, or spawning `circusd` as a
separate process and talking to it over ZeroMQ — which just relocates "a
process this app must supervise" one level, it doesn't remove it. `circus`
and `supervisord`/`s6` are also built around a human/CLI/config-file-first
workflow (an operator runs `circusctl`, edits an ini file); our actual
requirement is a library called from async route handlers, driven by our
own SQLite state (`ProviderConnection`/manifest `enabled`), with a modest
feature set (spawn, `GET /health` poll, restart-with-backoff, graceful
shutdown). None of the three fit cleanly enough to be worth the extra
runtime dependency and dual-event-loop risk, so `plugins/process_manager.py`
is a small, purpose-built `asyncio.create_subprocess_exec` supervisor — see
that module for the implementation.

## Data layer (concrete)

SQLite only — no Postgres/Mongo sidecar. `fastapi_mongo_base.sql.models
.BaseEntity` + SQLAlchemy (the same foundation already proven in the
project's earlier `apps/api` work) is the ORM. One `.sqlite3` file lives in
a named Docker volume alongside the generated credential-encryption key.

## Correctness & consistency (concrete)

Two things make this system easy to get subtly wrong, and both are called
out explicitly so nothing downstream "forgets" them: **the whole app is
async**, and **the `resources` table is the database UMedia's own users
interact with** — not a disposable cache. If it drifts from what a plugin
actually did, the user sees wrong file listings, broken downloads, or lost
data. Concretely:

1. **Never block the event loop.**
   - All DB access is async (`aiosqlite` via SQLAlchemy's async session) —
     already the plan, no sync DB calls anywhere, including inside
     background/scheduler jobs.
   - Any subprocess used by a plugin (`rclone cat`/`rcat`, etc.) uses
     `asyncio.create_subprocess_exec` with async pipe reads — never
     `subprocess.run`/blocking reads inside a request handler.
   - Every plugin RPC (`client.py`, Phase 2) sets **explicit connect/read/
     write timeouts** on the `httpx.AsyncClient`. A wedged or crashed plugin
     process must fail the specific request quickly (surfaced as the
     existing `ProviderConnectionError` shape), not hang the worker.
   - CPU-bound work on large payloads (hashing, MIME sniffing) stays
     chunked/streamed, matching `apps/media`'s existing `FileHashCalculator`/
     `FileValidator`; only escalate to `run_in_executor` if profiling
     actually shows it blocking the loop — don't pre-optimize.

2. **The `resources` row lifecycle is the consistency mechanism, not an
   afterthought.** Every write that touches a plugin follows the pattern
   `apps/media` already uses for background URL uploads
   (`FileStatus.processing` → `completed`/`failed`), generalized to *every*
   plugin-backed write, not just the async-upload path:
   - Create/update the `Resource` row as `status: processing` **before**
     calling the plugin.
   - Call the plugin.
   - **Verify, don't assume** — on a successful plugin response, confirm
     with a cheap follow-up (`stat`/`get`) before marking `completed`,
     mirroring `apps/media`'s existing post-upload `file_exists` check. A
     plugin returning HTTP 200 is not sufficient proof bytes actually
     landed.
   - On any failure (plugin error, timeout, verification mismatch), set
     `status: failed` + `error` — never silently leave a row pointing at
     content that was never actually written, and never leave it stuck in
     `processing` forever (the DB row must always end in a terminal,
     truthful state).
   - Deletes soft-delete the row first (already the design); the physical
     delete-through-the-plugin only happens on hard-delete, and if *that*
     call fails, the row stays in a visibly-errored, recoverable state
     instead of the row disappearing while remote bytes still exist (or
     vice versa).

3. **Writes are not blindly retried.** Read operations (`list`/`get`/
   content-read) may be retried with backoff on timeout — they're
   idempotent by nature. `create`/`update`/`delete` are **not**
   auto-retried by the plugin client, to avoid duplicate side effects
   (e.g. two uploads because the first response was merely slow); a failed
   write surfaces as `status: failed` per point 2 and is a user- or
   caller-initiated retry, not an automatic one.

4. **SQLite concurrency**: enable `PRAGMA journal_mode=WAL` and a sane
   `busy_timeout` at connection setup (SQLite serializes writers; WAL lets
   readers proceed during a write). Keep write transactions short — commit
   the `processing` row *before* the slow plugin call starts, then open a
   second short transaction to finalize `completed`/`failed`. Never hold a
   DB transaction open across a network/subprocess call.

5. **Event-loop discipline for anything holding subprocess transports.**
   In production there's one event loop for the app's whole lifetime
   (uvicorn's), so this doesn't come up — but `PluginProcessManager` holds
   real `asyncio.subprocess` transports, which are bound to whichever loop
   was running when they were created. A test fixture that creates one
   under pytest-asyncio's *session*-scoped fixture-loop default (this
   project's `asyncio_default_fixture_loop_scope = session`) while the
   test itself runs in the default *function*-scoped loop will fail
   tearing it down ("Future attached to a different loop") — pin such
   fixtures to `@pytest_asyncio.fixture(loop_scope="function")` explicitly
   rather than relying on the project default. Found and fixed this while
   building `plugins/process_manager.py` (Phase 2); recorded here so it
   isn't rediscovered the hard way in Phase 3's plugin contract tests.
6. **This is what the TDD requirement in `docs/08-implementation-plan.md`
   is actually protecting.** The shared provider-plugin contract test suite
   (Phase 2) and the `Resource` service tests (Phase 4, dedup/versioning/
   soft-delete against a fake repository first) exist specifically so this
   lifecycle is verified mechanically before real plugins are wired in —
   not left to manual review after the fact.

## Auth (concrete)

`usso.lite` (`~/Projects/pkgs/usso`, module `usso.lite`) provides local,
single-process identity — Argon2 password hashing, Ed25519-signed JWTs,
refresh sessions, OTP, rate limiting — without a separate identity server.
It is mounted directly into the core process (not a plugin, not isolated —
it's part of the trusted core). A thin wrapper issues the same httponly
session cookie the frontend already expects. See `docs/08-implementation-plan.md`
for the full rationale and `docs/AUTHENTICATION_DESIGN.md` (superseded, kept
for reference) for the auth flow this replaces.

Roles are `admin | user`; an unauthenticated request has no session. Bootstrap
setup creates the first admin exactly once (password only — OIDC is not
offered during setup). Admins manage users via `/users`. When Google OAuth
client credentials are configured, existing users may also sign in via
**OIDC identity** (`POST /auth/oidc/start` + `/complete`, openid/email/profile
scopes). That is separate from **Google Drive storage OAuth** under
`/providers/oauth/*` (Drive scopes); both may share the same Google Cloud
OAuth client but must not be merged. OIDC signup is disabled — only
pre-created accounts can log in.

Provider connections are **per-user** (`owner_id`); each authenticated user
only lists and manages their own. **Local filesystem storage** (`provider_type
= local`) is admin-only to create and use. Future remote deployments using
full `usso` tokens may use fine-grained scopes, while `usso.lite` remains the
default for a single-container deployment. `workspace_id` is reserved on
resources.
