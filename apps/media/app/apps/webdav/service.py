"""WebDAV verbs on top of `MediaFileService`.

No business rule lives here: ACL, placement, mirror-to-provider, trash and
the dual-layer write path are all the media service's. This module only
maps WebDAV paths/headers/statuses onto it.
"""

import asyncio
import mimetypes
import uuid
from pathlib import Path

from fastapi import Request
from fastapi.responses import Response, StreamingResponse

from apps.media_files.content_type import serve_content_type
from apps.media_files.errors import (
    MediaFileNotFoundError,
)
from apps.media_files.schemas import MediaFileRecord
from apps.media_files.services import MediaFileService

from . import xml
from .locks import Lock, LockTable, submitted_tokens
from .paths import BadPathError, clean_segment, destination_path, etag, href, http_date
from .tree import DavTree, Node

ALLOWED = (
    "OPTIONS, GET, HEAD, PUT, DELETE, MKCOL, COPY, MOVE, "
    "PROPFIND, PROPPATCH, LOCK, UNLOCK"
)
COPY_NODE_BUDGET = 5000


class DavError(Exception):
    def __init__(
        self,
        status: int,
        message: str = "",
        *,
        condition: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.condition = condition
        self.headers = headers or {}

    def response(self) -> Response:
        if self.condition:
            return Response(
                xml.error_body(self.condition),
                status_code=self.status,
                media_type="application/xml",
                headers=self.headers,
            )
        return Response(
            self.message.encode(),
            status_code=self.status,
            media_type="text/plain",
            headers=self.headers,
        )


def byte_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Single `bytes=` range -> inclusive (start, end); None = serve it all
    (no/unsupported/multi range). Raises DavError(416) when unsatisfiable."""
    if not header or not header.startswith("bytes=") or "," in header or size == 0:
        return None
    start_text, _, end_text = header.removeprefix("bytes=").strip().partition("-")
    try:
        if start_text == "":
            length = int(end_text)
            if length <= 0:
                raise ValueError  # noqa: TRY301
            return max(size - length, 0), size - 1
        start = int(start_text)
        end = min(int(end_text), size - 1) if end_text else size - 1
    except ValueError:
        return None
    if start >= size or start > end:
        raise DavError(
            416, "Range not satisfiable", headers={"Content-Range": f"bytes */{size}"},
        )
    return start, end


class DavService:
    def __init__(
        self,
        *,
        user_id: str,
        is_admin: bool,
        media: MediaFileService,
        locks: LockTable,
        base: str,
        host: str | None,
        staging_dir: Path,
        max_upload: int,
        lock_timeout: int,
    ) -> None:
        self._user_id = user_id
        self._is_admin = is_admin
        self._media = media
        self._tree = DavTree(media, user_id)
        self._locks = locks
        self._base = base
        self._host = host
        self._staging_dir = staging_dir
        self._max_upload = max_upload
        self._lock_timeout = lock_timeout

    # -- plumbing ---------------------------------------------------------

    def _href(self, node: Node) -> str:
        return href(self._base, list(node.segments), collection=node.is_collection)

    def _guard(self, request: Request, node_path: str, *, subtree: bool) -> None:
        tokens = submitted_tokens(
            request.headers.get("if"), request.headers.get("lock-token"),
        )
        for lock in self._locks.covering(self._user_id, node_path, subtree=subtree):
            if lock.token not in tokens:
                raise DavError(423, "Resource is locked")

    async def _require(self, segments: list[str]) -> Node:
        node = await self._tree.resolve(segments)
        if node is None:
            raise DavError(404, "Not found")
        return node

    async def _parent_for_new(self, segments: list[str]) -> tuple[Node, str]:
        """Existing collection that will hold `segments[-1]` (409 if not)."""
        parent = await self._tree.resolve(segments[:-1])
        if parent is None or not parent.is_collection:
            raise DavError(409, "Parent collection does not exist")
        return parent, segments[-1]

    # -- verbs ------------------------------------------------------------

    async def propfind(self, request: Request, segments: list[str]) -> Response:
        depth = request.headers.get("depth", "1").strip().lower()
        if depth == "infinity":
            raise DavError(403, "Depth infinity is not supported",
                           condition="propfind-finite-depth")
        if depth not in {"0", "1"}:
            raise DavError(400, "Invalid Depth header")
        try:
            wanted = xml.parse_propfind(await request.body())
        except xml.BadXmlError as exc:
            raise DavError(400, str(exc)) from exc
        node = await self._require(segments)
        nodes = [node]
        if depth == "1" and node.is_collection:
            nodes += await self._tree.children(node)
        entries = [
            xml.response_entry(
                self._href(item),
                xml.live_props(
                    item.record,
                    self._locks.at(self._user_id, item.path),
                    lock_timeout=self._lock_timeout,
                ),
                wanted,
            )
            for item in nodes
        ]
        return Response(
            xml.multistatus(entries), status_code=207, media_type="application/xml",
        )

    async def proppatch(self, request: Request, segments: list[str]) -> Response:
        node = await self._require(segments)
        self._guard(request, node.path, subtree=False)
        try:
            props = xml.parse_proppatch(await request.body())
        except xml.BadXmlError as exc:
            raise DavError(400, str(exc)) from exc
        entry = xml.proppatch_entry(self._href(node), props)
        return Response(
            xml.multistatus([entry]), status_code=207, media_type="application/xml",
        )

    def _file_headers(self, record: MediaFileRecord) -> dict[str, str]:
        return {
            "Content-Type": serve_content_type(record.name, record.content_type),
            "ETag": etag(record),
            "Last-Modified": http_date(record.updated_at),
            "Accept-Ranges": "bytes",
        }

    async def get(
        self, request: Request, segments: list[str], *, head: bool,
    ) -> Response:
        node = await self._require(segments)
        record = node.record
        if record is None or node.is_collection:
            raise DavError(405, "Collections cannot be fetched",
                           headers={"Allow": ALLOWED})
        headers = self._file_headers(record)
        if request.headers.get("if-none-match") in {headers["ETag"], "*"}:
            return Response(status_code=304, headers={"ETag": headers["ETag"]})
        bounds = byte_range(request.headers.get("range"), record.size)
        status = 206 if bounds else 200
        length = bounds[1] - bounds[0] + 1 if bounds else record.size
        headers["Content-Length"] = str(length)
        if bounds:
            headers["Content-Range"] = f"bytes {bounds[0]}-{bounds[1]}/{record.size}"
        if head:
            return Response(status_code=status, headers=headers)
        try:
            _, stream = await self._media.read_content(
                record.uid,
                actor_user_id=self._user_id,
                range_header=f"bytes={bounds[0]}-{bounds[1]}" if bounds else None,
            )
        except MediaFileNotFoundError as exc:
            raise DavError(404, "Not found") from exc
        return StreamingResponse(stream, status_code=status, headers=headers)

    def _check_size(self, size: int) -> None:
        if size > self._max_upload:
            raise DavError(413, "Upload too large")

    async def _stage(self, request: Request) -> tuple[Path, int]:
        """Stream the request body to disk, never into memory, enforcing the
        size cap on the bytes actually received (not just Content-Length)."""
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > self._max_upload:
            raise DavError(413, "Upload too large")
        await asyncio.to_thread(self._staging_dir.mkdir, parents=True, exist_ok=True)
        path = self._staging_dir / f"dav-{uuid.uuid4().hex}.part"
        size = 0
        try:
            with path.open("wb") as out:
                async for chunk in request.stream():
                    size += len(chunk)
                    self._check_size(size)
                    await asyncio.to_thread(out.write, chunk)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return path, size

    async def put(self, request: Request, segments: list[str]) -> Response:
        if not segments:
            raise DavError(405, "Cannot PUT to the root", headers={"Allow": ALLOWED})
        parent, name = await self._parent_for_new(segments)
        existing = await self._tree.child(parent, name)
        if existing is not None and existing.is_collection:
            raise DavError(405, "A collection exists at this path",
                           headers={"Allow": ALLOWED})
        self._guard(request, "/" + "/".join(segments), subtree=False)
        self._check_preconditions(request, existing)
        staged, _ = await self._stage(request)
        content_type = request.headers.get("content-type") or (
            mimetypes.guess_type(name)[0]
        )
        try:
            content = await asyncio.to_thread(staged.read_bytes)
            if existing is not None and existing.record is not None:
                await self._media.replace_content(
                    existing.record.uid,
                    content=content,
                    content_type=content_type,
                    actor_user_id=self._user_id,
                )
                return Response(status_code=204)
            await self._media.upload(
                parent_id=parent.parent_id,
                name=name,
                content=content,
                content_type=content_type,
                owner_id=self._user_id,
                is_admin=self._is_admin,
            )
        finally:
            await asyncio.to_thread(staged.unlink, missing_ok=True)
        return Response(status_code=201)

    @staticmethod
    def _check_preconditions(request: Request, existing: Node | None) -> None:
        if_match = request.headers.get("if-match")
        if_none_match = request.headers.get("if-none-match")
        current = etag(existing.record) if existing and existing.record else None
        if if_none_match is not None and existing is not None and (
            if_none_match.strip() == "*" or current in if_none_match
        ):
            raise DavError(412, "Precondition failed")
        if if_match is not None and (
            existing is None or (if_match.strip() != "*" and current not in if_match)
        ):
            raise DavError(412, "Precondition failed")

    async def mkcol(self, request: Request, segments: list[str]) -> Response:
        if await request.body():
            raise DavError(415, "MKCOL bodies are not supported")
        if not segments:
            raise DavError(405, "Already exists", headers={"Allow": ALLOWED})
        parent, name = await self._parent_for_new(segments)
        if await self._tree.child(parent, name) is not None:
            raise DavError(405, "Already exists", headers={"Allow": ALLOWED})
        self._guard(request, "/" + "/".join(segments), subtree=False)
        await self._media.create_folder(
            name=name,
            parent_id=parent.parent_id,
            owner_id=self._user_id,
            is_admin=self._is_admin,
        )
        return Response(status_code=201)

    async def delete(self, request: Request, segments: list[str]) -> Response:
        node = await self._require(segments)
        if node.record is None:
            raise DavError(403, "The root cannot be deleted")
        self._guard(request, node.path, subtree=True)
        # Soft delete: the item (and its subtree) goes to the trash.
        await self._media.soft_delete(node.record.uid, actor_user_id=self._user_id)
        self._locks.remove_under(self._user_id, node.path)
        return Response(status_code=204)

    async def _destination(
        self, request: Request, source: Node,
    ) -> tuple[list[str], Node, str, Node | None]:
        header = request.headers.get("destination")
        if not header:
            raise DavError(400, "Destination header is required")
        decoded = destination_path(header, host=self._host, base=self._base)
        if decoded is None:
            raise DavError(502, "Destination is outside this WebDAV mount")
        try:
            target = [clean_segment(part) for part in decoded.split("/") if part]
        except BadPathError as exc:
            raise DavError(400, "Invalid destination path") from exc
        if not target:
            raise DavError(403, "Cannot overwrite the root")
        if tuple(target) == source.segments:
            raise DavError(403, "Source and destination are the same")
        if source.is_collection and tuple(target[: len(source.segments)]) == (
            source.segments
        ):
            raise DavError(403, "Cannot copy or move a collection into itself")
        parent, name = await self._parent_for_new(target)
        return target, parent, name, await self._tree.child(parent, name)

    async def _clear_destination(
        self, request: Request, target: list[str], existing: Node | None,
    ) -> bool:
        """Apply the Overwrite header; True when something was replaced."""
        self._guard(request, "/" + "/".join(target), subtree=True)
        if existing is None or existing.record is None:
            return False
        if request.headers.get("overwrite", "T").strip().upper() == "F":
            raise DavError(412, "Destination exists and Overwrite is F")
        await self._media.soft_delete(
            existing.record.uid, actor_user_id=self._user_id,
        )
        return True

    async def move(self, request: Request, segments: list[str]) -> Response:
        source = await self._require(segments)
        if source.record is None:
            raise DavError(403, "The root cannot be moved")
        self._guard(request, source.path, subtree=True)
        target, parent, name, existing = await self._destination(request, source)
        replaced = await self._clear_destination(request, target, existing)
        await self._media.update(
            source.record.uid,
            actor_user_id=self._user_id,
            name=name,
            parent_id=parent.parent_id,
        )
        self._locks.remove_under(self._user_id, source.path)
        return Response(status_code=204 if replaced else 201)

    async def copy(self, request: Request, segments: list[str]) -> Response:
        source = await self._require(segments)
        if source.record is None:
            raise DavError(403, "The root cannot be copied")
        depth = request.headers.get("depth", "infinity").strip().lower()
        if depth not in {"0", "infinity"}:
            raise DavError(400, "Invalid Depth header")
        target, parent, name, existing = await self._destination(request, source)
        replaced = await self._clear_destination(request, target, existing)
        budget = [COPY_NODE_BUDGET]
        await self._copy_tree(
            source, parent.parent_id, name, shallow=depth == "0", budget=budget,
        )
        return Response(status_code=204 if replaced else 201)

    async def _cross_storage(
        self, source: MediaFileRecord, dest_parent_id: str | None,
    ) -> bool:
        """Same rule as `TransferService._is_cross_storage`: bytes are only
        re-streamed when both ends name different connections."""
        if dest_parent_id is None or source.provider_connection_id is None:
            return False
        dest = await self._media._bound_connection(dest_parent_id)
        return dest is not None and dest != source.provider_connection_id

    async def _copy_tree(
        self,
        source: Node,
        dest_parent_id: str | None,
        name: str,
        *,
        shallow: bool,
        budget: list[int],
    ) -> None:
        budget[0] -= 1
        if budget[0] < 0:
            raise DavError(507, "Copy is too large for one request")
        record = source.record
        if record is None:
            return
        if record.type != "folder":
            await self._media.copy_file(
                record,
                dest_parent_id=dest_parent_id,
                name=name,
                actor_user_id=self._user_id,
                byte_copy=await self._cross_storage(record, dest_parent_id),
                is_admin=self._is_admin,
            )
            return
        created = await self._media.create_folder(
            name=name,
            parent_id=dest_parent_id,
            owner_id=self._user_id,
            is_admin=self._is_admin,
        )
        if shallow:
            return
        for child in await self._tree.children(source):
            await self._copy_tree(
                child, created.uid, child.segments[-1], shallow=False, budget=budget,
            )

    async def lock(self, request: Request, segments: list[str]) -> Response:
        path = "/" + "/".join(segments) if segments else ""
        tokens = submitted_tokens(request.headers.get("if"), None)
        if not (await request.body()).strip():
            for token in tokens:
                refreshed = self._locks.refresh(
                    token, self._user_id, self._lock_timeout,
                )
                if refreshed is not None:
                    return self._lock_reply(refreshed, 200)
            raise DavError(412, "No lock to refresh")
        try:
            owner = xml.parse_lockinfo_owner(await request.body())
        except xml.BadXmlError as exc:
            raise DavError(400, str(exc)) from exc
        if await self._tree.resolve(segments) is not None:
            status = 200
        elif segments:
            await self._parent_for_new(segments)  # 409 when parent is missing
            status = 201
        else:
            status = 200
        if self._locks.covering(self._user_id, path, subtree=True):
            raise DavError(423, "Resource is already locked")
        depth = request.headers.get("depth", "infinity").strip().lower()
        lock = self._locks.create(
            user_id=self._user_id,
            path=path,
            depth_infinity=depth != "0",
            owner_xml=owner or "",
            timeout=self._lock_timeout,
        )
        return self._lock_reply(lock, status)

    def _lock_reply(self, lock: Lock, status: int) -> Response:
        return Response(
            xml.lock_response(lock, timeout=self._lock_timeout),
            status_code=status,
            media_type="application/xml",
            headers={"Lock-Token": f"<{lock.token}>"},
        )

    async def unlock(self, request: Request) -> Response:
        token = next(
            iter(submitted_tokens(None, request.headers.get("lock-token"))), None,
        )
        if token is None or not self._locks.remove(token, self._user_id):
            raise DavError(
                409,
                "Unknown lock token",
                condition="lock-token-matches-request-uri",
            )
        return Response(status_code=204)
