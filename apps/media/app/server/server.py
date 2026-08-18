"""FastAPI application factory."""

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import fastapi_mongo_base.sql.models as sql_models
import fastapi_mongo_base.sql.session as sql_session
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi_mongo_base.core import app_factory
from sqlalchemy import text

from apps.auth.config import build_lite_config
from apps.auth.middleware import jwt_guard
from apps.auth.routes import router as auth_router
from apps.auth.services import AuthService
from apps.auth.user_routes import router as users_router
from apps.media_files.routes import provider_index_router
from apps.media_files.routes import public_router as media_files_public_router
from apps.media_files.routes import router as media_files_router
from apps.media_files.uploads import build_upload_router, run_periodic_cleanup
from apps.media_files.worker import purge_expired_trash
from apps.provider_connections.routes import router as provider_connections_router
from apps.s3.routes import register_s3_exception_handler
from apps.s3.routes import root_router as s3_root_router
from apps.s3.routes import router as s3_router
from apps.s3.vhost import S3VirtualHostRewriteMiddleware
from apps.user_access_keys.routes import router as access_keys_router
from apps.user_access_keys.factory import (
    build_user_access_key_service_from_state,
)
from plugins.process_manager import PluginProcessManager, PluginStartError
from plugins.registry import PluginRegistry
from utils.crypto import CredentialCipher

from . import config
from .database import create_engine, create_session_factory


def _plugin_env(process_key: str, settings: config.Settings) -> dict[str, str]:
    """The environment allowlist for one plugin process (see
    `plugins.process_manager`'s isolation model -- a plugin gets nothing
    beyond this and a bare `PATH`)."""
    if process_key == "local":
        return {"UMEDIA_LOCAL_STORAGE_ROOT": str(settings.local_storage_root)}
    return {}


async def _start_plugins(
    registry: PluginRegistry,
    process_manager: PluginProcessManager,
    settings: config.Settings,
) -> None:
    """Spawn every plugin process the enabled manifests need.

    A plugin that fails to start (e.g. a missing `rclone` binary) is
    logged and skipped, not fatal to the whole app -- one broken provider
    type shouldn't take down every other one.
    """
    for process_key in registry.process_ids():
        entrypoint = registry.entrypoint_for(process_key)
        if entrypoint is None:
            continue
        try:
            await process_manager.start(
                process_key, entrypoint, env=_plugin_env(process_key, settings),
            )
        except PluginStartError:
            logging.exception("Plugin '%s' failed to start", process_key)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Initialize application services."""
    settings = config.Settings()

    engine = create_engine(settings.database_uri)
    session_factory = create_session_factory(engine)
    sql_models.async_session = session_factory
    sql_session.async_session = session_factory
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.credential_cipher = CredentialCipher.from_environment(
        data_dir=settings.data_dir,
        env_key=settings.master_key,
    )
    # `apps.resources.uploads`'s tus completion hook schedules detached
    # background tasks here; keeping the reference is required (asyncio
    # only holds a weak one) and this is also what a graceful shutdown
    # below waits on, so an in-flight upload finishes writing instead of
    # being killed mid-plugin-call.
    app.state.tus_finalize_tasks = set()
    # Background import jobs scheduled after `POST /providers` with
    # `import_existing` (apps/provider_connections/routes.py) -- same
    # keep-a-strong-reference + wait-on-shutdown deal as the tus tasks.
    app.state.import_tasks = set()
    # Connection uids currently running an explicit `POST .../sync` so a
    # second click returns 409 instead of stacking walks.
    app.state.active_syncs = set()

    # Every new account (bootstrap admin included) gets a default access
    # key pair -- what signs its temporary share links.
    auth_service = AuthService(
        build_lite_config(settings),
        access_keys=build_user_access_key_service_from_state(app.state),
    )
    await auth_service.ensure_initialized()
    app.state.auth_service = auth_service

    plugin_registry = PluginRegistry(settings.plugins_dir)
    plugin_process_manager = PluginProcessManager(settings.plugin_socket_dir)
    await _start_plugins(plugin_registry, plugin_process_manager, settings)
    app.state.plugin_registry = plugin_registry
    app.state.plugin_process_manager = plugin_process_manager

    tus_cleanup_task = asyncio.create_task(
        run_periodic_cleanup(settings.tus_upload_dir),
    )

    # Nightly library maintenance -- same cron pattern the legacy
    # `server/worker.py` used for the Mongo-era `apps.files`, but wired
    # into this app's own lifespan and pointed at the MediaFile layer:
    # purge trash items soft-deleted more than 30 days ago, at midnight
    # Tehran time.
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        purge_expired_trash,
        "cron",
        hour=0,
        minute=0,
        timezone=pytz.timezone("Asia/Tehran"),
        args=[session_factory],
    )
    scheduler.start()

    logging.info("Startup complete")
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        tus_cleanup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await tus_cleanup_task
        background_tasks = app.state.tus_finalize_tasks | app.state.import_tasks
        if background_tasks:
            # Bounded, same rationale as PluginProcessManager's graceful
            # stop: let in-flight uploads/imports finish writing rather
            # than be cut off mid-plugin-call, but never hang shutdown.
            _done, pending = await asyncio.wait(background_tasks, timeout=10.0)
            for task in pending:
                task.cancel()
        await plugin_process_manager.stop_all()
        await auth_service.dispose()
        await engine.dispose()
        logging.info("Shutdown complete")


app = app_factory.create_app(
    settings=config.Settings(),
    # Install CORS ourselves so we can allow https://*.uln.me via regex.
    origins=[],
    lifespan_func=lifespan,
    # We register our own richer /api/v1/health below (with a DB
    # round-trip); the framework default would otherwise be registered
    # first and silently shadow it, since Starlette matches routes in
    # registration order.
    health_route=False,
    # Framework default redirects `/` to `/api/v1/docs`. Cyberduck lists
    # buckets at `GET /` on this host; that 307 would be HTML to an S3 client.
    index_route=False,
)
app.middleware("http")(jwt_guard)

_cors_regex = os.getenv("CORS_ORIGIN_REGEX", r"https://.*\.uln\.me").strip() or None
_cors_origins = config.Settings().cors_origins or ["http://localhost:8000"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=_cors_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Outermost: rewrite `{bucket}.{host}/{key}` before FastAPI routes or jwt
# see the path. Auth reads the original path from the ASGI scope.
app.add_middleware(S3VirtualHostRewriteMiddleware)


server_router = APIRouter()
server_router.include_router(auth_router)
server_router.include_router(users_router)
server_router.include_router(access_keys_router)
server_router.include_router(provider_connections_router)
server_router.include_router(media_files_router)
server_router.include_router(media_files_public_router)
server_router.include_router(s3_router, prefix="/s3")
server_router.include_router(provider_index_router)
server_router.include_router(
    build_upload_router(upload_dir=config.Settings().tus_upload_dir),
)
# Two superseded packages stay deliberately unmounted but untouched on
# disk: `apps.files` (the pre-rebuild Beanie code) and `apps.resources`
# (the single-`resources` model that coupled library structure to the
# provider -- replaced by the MediaFile/StorageObject dual layer, see
# docs/11-dual-layer-library.md; its `resources` table also remains until
# a later cleanup migration).

app.include_router(server_router, prefix=config.Settings.base_path)
# Cyberduck lists at `GET /` on the website hostname (no `/api/v1/s3`).
app.include_router(s3_root_router)

# S3 clients expect the gateway's errors (auth failures included) as
# S3-style XML, not the JSON error envelope the rest of the API uses.
register_s3_exception_handler(app)


@app.get("/api/v1/health", tags=["System"])
async def health(request: Request) -> dict[str, str]:
    """Liveness + database round-trip."""
    async with request.app.state.session_factory() as session:
        await session.execute(text("SELECT 1"))
    return {"service": "umedia-media", "status": "ok", "database": "ok"}


@app.exception_handler(404)
def not_found(_: Request, __: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={
            "code": "not_found",
            "message": "Resource not found",
            "details": {},
        },
    )
