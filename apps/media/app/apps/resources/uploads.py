"""Resumable (tus) uploads: `POST/HEAD/PATCH/OPTIONS/DELETE /uploads/*`.

Why tus at all: a plain `POST /resources` (apps/resources/routes.py) reads
the whole upload into memory before responding, over one HTTP request that
must complete fully for the browser to hear anything back -- painful for
large files and impossible to resume after a dropped connection. tus
(https://tus.io) fixes this with chunked, resumable uploads; `tuspyserver`
(pinned exact in pyproject.toml, audited before pinning -- see that
comment) provides the protocol server as a mountable `APIRouter`, so no
extra process/proxy is needed the way a subprocess-based server (tusd)
would require.

This module is deliberately a thin adapter, not a reimplementation of
`ResourceService.create()`: tus's job ends at "the bytes are fully staged
on local disk," at which point `_finalize()` calls the exact same
`ResourceService.create()` every other upload path uses (dedup, the
`processing`->verify->`completed`/`failed` lifecycle, all of it) -- just
reading from a staged file instead of a request body already in memory.

Flow:
  1. Browser (`tus-js-client`) creates an upload with `Upload-Metadata`
     carrying `provider_connection_id`/`name`/`type`/`parent_id`.
  2. `_pre_create_dep`'s hook validates that metadata *before* accepting
     any bytes -- fail fast, don't stage bytes for a doomed upload.
  3. The browser PATCHes chunks in; tuspyserver streams each straight to
     disk (never buffers a whole chunk-less request in memory) and is
     resumable across a dropped connection.
  4. Once fully staged, `_upload_complete_dep`'s hook fires. It does *not*
     await the actual resource creation -- it schedules `_finalize()` as a
     detached background task and returns immediately, which is what
     keeps the browser's final PATCH response fast instead of blocking on
     a plugin write. The frontend sees the new resource show up as
     `processing` on its next `GET /resources` poll, then flip to
     `completed`/`failed` -- the same lifecycle every other write already
     has, nothing new for it to handle.
"""

import asyncio
import json
import logging
import mimetypes
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request, status
from tuspyserver import create_tus_router

from apps.provider_connections.repository import ProviderConnectionRepository

from .factory import build_resource_service_from_state

logger = logging.getLogger(__name__)

UPLOAD_PREFIX = "uploads"

# How long an *incomplete* upload's staged bytes stick around before
# automatic cleanup -- deliberately generous (matches Google Drive's own
# resumable-session lifetime, longer than tuspyserver's 5-day default):
# losing a large, mostly-finished upload because the admin paused it over
# a long weekend defeats half the point of using tus. Passed to
# `create_tus_router(days_to_keep=...)`, which stamps every upload's own
# `.info` file with the resulting expiry -- `_cleanup_expired_uploads`
# below is what actually *acts* on that stamp; `create_tus_router` only
# records it.
UPLOAD_RETENTION_DAYS = 7

# How often the cleanup sweep runs -- independent of the retention window
# above (frequency vs. duration are different knobs). Hourly is cheap (a
# `glob` over what should normally be a handful of files) and keeps
# actual disk usage close to the nominal retention window rather than
# lagging it by up to a full sweep interval on top.
CLEANUP_INTERVAL_SECONDS = 60 * 60


async def _validate_metadata(request: Request, metadata: dict[str, str]) -> None:
    """Fail fast, before any bytes are staged: the fields
    `ResourceService.create()` cannot proceed without, plus that the
    connection actually exists (a typo'd/deleted uid would otherwise only
    surface as a `failed` resource *after* the whole upload finished).

    `filetype` is required here for a reason that has nothing to do with
    our own code: `tuspyserver`'s *own* `HEAD /uploads/{id}` route (used
    by every tus client to resume after a dropped connection) hard-requires
    a `filetype`/`type` metadata key and 400s without one -- undocumented
    in its README, found by writing `test_resource_uploads.py`'s
    resumability test. A plain create-then-PATCH-to-completion upload
    that never calls HEAD would work without it, but any real client that
    resumes will eventually hit that 400, so this rejects it upfront
    instead of only failing on some later resume attempt.
    """
    if not metadata.get("name"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Upload-Metadata must include 'name'",
        )
    if not metadata.get("filetype"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Upload-Metadata must include 'filetype'",
        )
    provider_connection_id = metadata.get("provider_connection_id")
    if not provider_connection_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Upload-Metadata must include 'provider_connection_id'",
        )
    connections = ProviderConnectionRepository(request.app.state.session_factory)
    if await connections.get(provider_connection_id) is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unknown provider_connection_id '{provider_connection_id}'",
        )


async def _finalize(
    app: FastAPI, file_path: str, metadata: dict[str, str], *, owner_id: str,
) -> None:
    """Runs detached, after the hook that scheduled it has already
    returned -- see module docstring. Never raises (there is no request
    left to raise *to*); the terminal outcome lives entirely in the
    `Resource` row's own `status`/`error` fields, same as every other
    write's failure path.

    `owner_id` is the *authenticated uploader's* uid, captured from
    `request.state.user` by the completion hook -- deliberately not a
    `Upload-Metadata` field, which the client controls and could use to
    plant resources under someone else's account."""
    path = Path(file_path)
    try:
        content = await asyncio.to_thread(path.read_bytes)
        name = metadata.get("name") or path.name
        content_type = metadata.get("filetype") or mimetypes.guess_type(name)[0]
        service = build_resource_service_from_state(app.state)
        await service.create(
            provider_connection_id=metadata["provider_connection_id"],
            parent_id=metadata.get("parent_id") or None,
            name=name,
            type_=metadata.get("type") or "file",
            content=content,
            content_type=content_type,
            owner_id=owner_id,
        )
    except Exception:
        logger.exception("Finalizing tus upload '%s' failed", file_path)
    finally:
        await asyncio.to_thread(path.unlink, missing_ok=True)


def _parse_expires(expires: str | float) -> datetime | None:
    """`TusUploadParams.expires` is an RFC 7231 date string in normal
    operation (`_format_rfc7231_date` in tuspyserver's own route code),
    but the field's own type allows a raw float timestamp too -- accept
    both rather than assume."""
    try:
        if isinstance(expires, str):
            parsed = parsedate_to_datetime(expires)
        else:
            parsed = datetime.fromtimestamp(expires, tz=UTC)
    except (ValueError, TypeError, OverflowError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _cleanup_expired_uploads(upload_dir: Path) -> None:
    """Deletes any staged upload (data file + its `.info` sidecar) past
    its own recorded expiry.

    Not `tuspyserver.file.gc_files`: that function exists (per the
    package's own README) but isn't actually in the installed source --
    confirmed by grepping it before writing this, not assumed. Reading
    the *documented* on-disk shape directly (one `<uid>` data file + one
    `<uid>.info` JSON sidecar per upload, `.info` holding a serialized
    `TusUploadParams` with an `expires` field) is small enough to own
    outright rather than depend on an internal API that may not be
    stable -- see `tuspyserver.info.TusUploadInfo`/`params.TusUploadParams`.
    """
    if not upload_dir.is_dir():
        return
    now = datetime.now(UTC)
    for info_path in upload_dir.glob("*.info"):
        try:
            info = json.loads(info_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        expires = info.get("expires")
        if not expires:
            continue
        expires_at = _parse_expires(expires)
        if expires_at is None or expires_at >= now:
            continue
        data_path = upload_dir / info_path.stem
        data_path.unlink(missing_ok=True)
        info_path.unlink(missing_ok=True)
        logger.info("Removed expired tus upload '%s'", info_path.stem)


async def run_periodic_cleanup(upload_dir: Path) -> None:
    """Started as a background task from the app's lifespan; runs until
    cancelled at shutdown. A failed sweep is logged and retried next
    interval rather than killing the loop -- one bad `.info` file
    shouldn't stop cleanup for every other upload forever."""
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
        try:
            await asyncio.to_thread(_cleanup_expired_uploads, upload_dir)
        except Exception:
            logger.exception("Periodic tus upload cleanup sweep failed")


def _schedule_finalize(
    app: FastAPI, file_path: str, metadata: dict[str, str], *, owner_id: str,
) -> None:
    # Keeping a reference in `app.state.tus_finalize_tasks` is required, not
    # cosmetic: asyncio only holds a *weak* reference to a task created via
    # `create_task`, so an unreferenced one can be garbage-collected mid-run
    # (see the stdlib docs' own warning on this). `add_done_callback` prunes
    # the set once each task finishes instead of leaking it forever.
    task = asyncio.create_task(
        _finalize(app, file_path, metadata, owner_id=owner_id),
    )
    app.state.tus_finalize_tasks.add(task)
    task.add_done_callback(app.state.tus_finalize_tasks.discard)


def build_upload_router(*, upload_dir: Path) -> APIRouter:
    """The tus router, mounted at `/uploads` (→ `/api/v1/uploads` once
    included under `server/server.py`'s `server_router`) -- already
    behind `apps.auth.middleware.jwt_guard` like every other `/api/v1`
    route, so no separate `auth=` here."""

    def pre_create_dep(
        request: Request,
    ) -> Callable[[dict[str, str], dict[str, Any]], Awaitable[None]]:
        async def hook(metadata: dict[str, str], _upload_info: dict[str, Any]) -> None:
            await _validate_metadata(request, metadata)

        return hook

    def upload_complete_dep(
        request: Request,
    ) -> Callable[[str, dict[str, str]], None]:
        def hook(file_path: str, metadata: dict[str, str]) -> None:
            # The final PATCH is behind jwt_guard like every other
            # `/api/v1` route, so `request.state.user` is always set here
            # -- its uid becomes the resource's owner.
            _schedule_finalize(
                request.app,
                file_path,
                metadata,
                owner_id=request.state.user.uid,
            )

        return hook

    return create_tus_router(
        prefix=UPLOAD_PREFIX,
        files_dir=str(upload_dir),
        days_to_keep=UPLOAD_RETENTION_DAYS,
        pre_create_dep=pre_create_dep,
        upload_complete_dep=upload_complete_dep,
    )
