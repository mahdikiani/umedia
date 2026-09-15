"""Resumable (tus) uploads: `POST/HEAD/PATCH/OPTIONS/DELETE /uploads/*`.

The MediaFile-era adaptation of `apps/resources/uploads.py` -- see that
module's docstring for the full why-tus and why-each-quirk story (the
`filetype` requirement, detached finalize tasks, retention/cleanup). The
only behavioral change here: once the bytes are fully staged,
`_finalize()` calls `MediaFileService.upload()` -- the same dual-layer
path every other upload uses (plugin write -> StorageObject -> MediaFile
+ link) -- instead of the retired `ResourceService.create()`. The
frontend contract is unchanged: the new file shows up as `processing` on
the next `GET /files` poll, then flips to `completed`/`failed`.
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

from .factory import build_media_file_service_from_state

logger = logging.getLogger(__name__)

UPLOAD_PREFIX = "uploads"

# See apps/resources/uploads.py for the rationale on both knobs.
UPLOAD_RETENTION_DAYS = 7
CLEANUP_INTERVAL_SECONDS = 60 * 60


async def _validate_metadata(request: Request, metadata: dict[str, str]) -> None:
    """Fail fast, before any bytes are staged: the fields
    `MediaFileService.upload()` cannot proceed without, plus that the
    connection actually exists and is usable by the caller. `filetype`
    is required by tuspyserver's own resume (`HEAD`) route -- see
    apps/resources/uploads.py."""
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
    if provider_connection_id:
        from apps.provider_connections.services import connection_usable_by

        connections = ProviderConnectionRepository(request.app.state.session_factory)
        connection = await connections.get(provider_connection_id)
        user = request.state.user
        is_admin = "admin" in (user.roles or [])
        if connection is None or not connection_usable_by(
            connection,
            actor_user_id=user.uid,
            is_admin=is_admin,
        ):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Unknown provider_connection_id '{provider_connection_id}'",
            )


async def _finalize(
    app: FastAPI,
    file_path: str,
    metadata: dict[str, str],
    *,
    owner_id: str,
    is_admin: bool,
) -> None:
    """Runs detached, after the hook that scheduled it has returned.
    Never raises; the terminal outcome lives in the MediaFile row's own
    `status`/`error` fields. `owner_id` is the authenticated uploader's
    uid, captured by the completion hook -- deliberately not an
    `Upload-Metadata` field the client could forge."""
    path = Path(file_path)
    try:
        content = await asyncio.to_thread(path.read_bytes)
        name = metadata.get("name") or path.name
        content_type = metadata.get("filetype") or mimetypes.guess_type(name)[0]
        service = build_media_file_service_from_state(app.state)
        await service.upload(
            provider_connection_id=metadata.get("provider_connection_id") or None,
            parent_id=metadata.get("parent_id") or None,
            name=name,
            content=content,
            content_type=content_type,
            owner_id=owner_id,
            is_admin=is_admin,
        )
    except Exception:
        logger.exception("Finalizing tus upload '%s' failed", file_path)
    finally:
        await asyncio.to_thread(path.unlink, missing_ok=True)


def _parse_expires(expires: str | float) -> datetime | None:
    """See apps/resources/uploads.py -- accepts both the RFC 7231 string
    tuspyserver normally writes and the raw float its type allows."""
    try:
        if isinstance(expires, str):
            parsed = parsedate_to_datetime(expires)
        else:
            parsed = datetime.fromtimestamp(expires, tz=UTC)
    except (ValueError, TypeError, OverflowError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _cleanup_expired_uploads(upload_dir: Path) -> None:
    """Deletes any staged upload (data file + `.info` sidecar) past its
    own recorded expiry -- see apps/resources/uploads.py for why this is
    owned here instead of relying on tuspyserver internals."""
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
    cancelled at shutdown."""
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
        try:
            await asyncio.to_thread(_cleanup_expired_uploads, upload_dir)
        except Exception:
            logger.exception("Periodic tus upload cleanup sweep failed")


def _schedule_finalize(
    app: FastAPI,
    file_path: str,
    metadata: dict[str, str],
    *,
    owner_id: str,
    is_admin: bool,
) -> None:
    # asyncio holds only a weak reference to created tasks; the app.state
    # set keeps finalizes alive and lets shutdown wait on them.
    task = asyncio.create_task(
        _finalize(
            app, file_path, metadata, owner_id=owner_id, is_admin=is_admin,
        ),
    )
    app.state.tus_finalize_tasks.add(task)
    task.add_done_callback(app.state.tus_finalize_tasks.discard)


def build_upload_router(*, upload_dir: Path) -> APIRouter:
    """The tus router, mounted at `/uploads` -- already behind
    `apps.auth.middleware.jwt_guard` like every other `/api/v1` route."""

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
            user = request.state.user
            _schedule_finalize(
                request.app,
                file_path,
                metadata,
                owner_id=user.uid,
                is_admin="admin" in (user.roles or []),
            )

        return hook

    return create_tus_router(
        prefix=UPLOAD_PREFIX,
        files_dir=str(upload_dir),
        days_to_keep=UPLOAD_RETENTION_DAYS,
        pre_create_dep=pre_create_dep,
        upload_complete_dep=upload_complete_dep,
    )
