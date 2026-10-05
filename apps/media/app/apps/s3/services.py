"""S3 object operations projected from the visible MediaFile library tree.

Buckets: `Settings.S3_COMPAT_BUCKET` (default `umedia`) is the whole
library. Every enabled connection the key owner may use is also a bucket,
named after the connection (`apps/provider_connections/names.py` keeps
names bucket-safe). A connection bucket uses the same library-path keys
but only sees -- and only writes to -- files stored on that connection.
"""

from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import quote

from fastapi.responses import Response, StreamingResponse

from apps.media_files.content_type import serve_content_type
from apps.media_files.errors import (
    MediaFileNotFoundError,
    MediaFilePermissionError,
    MediaFileValidationError,
    MediaFileWriteFailedError,
)
from apps.media_files.schemas import MediaFileRecord
from apps.media_files.services import MediaFileService
from server.config import Settings

from .exceptions import (
    AccessDenied,
    InvalidArgument,
    NoSuchBucket,
    NoSuchKey,
)
from .paths import decode_key, encode_key, file_keys, folder_prefixes
from .xml import (
    ObjectItem,
    _timestamp,
    delete_result_xml,
    list_buckets_xml,
    list_objects_v2_xml,
    parse_delete_objects_body,
)

MAX_KEYS_CAP = 1000


class ConnectionListProtocol(Protocol):
    """The slice of `ProviderConnectionRepository` PutObject needs."""

    async def list(self, *, owner_id: str | None = None) -> list[Any]: ...


def _etag(record: MediaFileRecord) -> str:
    """S3-style quoted ETag -- the indexed content hash when the physical
    layer has one, otherwise the stable uid (never empty: clients treat a
    missing ETag worse than an opaque one)."""
    return f'"{record.content_hash or record.uid}"'


def _metadata_headers(record: MediaFileRecord) -> dict[str, str]:
    return {
        "Content-Type": serve_content_type(record.name, record.content_type),
        "Content-Length": str(record.size),
        "Content-Disposition": (
            f"inline; filename*=UTF-8''{quote(record.name)}"
        ),
        "ETag": _etag(record),
        "Accept-Ranges": "bytes",
        "Last-Modified": record.updated_at.strftime(
            "%a, %d %b %Y %H:%M:%S GMT",
        ),
    }


# `None` scope = the whole-library bucket; otherwise a connection uid.
Scope = str | None


def _in_scope(record: MediaFileRecord, scope: Scope) -> bool:
    return scope is None or record.provider_connection_id == scope


class S3ObjectService:
    """S3 operations over the key owner's visible slice of the library."""

    def __init__(
        self,
        *,
        user_id: str,
        media_files: MediaFileService,
        connections: ConnectionListProtocol,
        is_admin: bool = False,
    ) -> None:
        self._user_id = user_id
        self._media_files = media_files
        self._connections = connections
        self._is_admin = is_admin

    async def _connection_buckets(self) -> dict[str, Any]:
        """Bucket name -> connection, for connections the owner may use."""
        from apps.provider_connections.services import connection_usable_by

        connections = await self._connections.list(owner_id=self._user_id)
        return {
            connection.name: connection
            for connection in connections
            if getattr(connection, "enabled", True)
            and connection_usable_by(
                connection,
                actor_user_id=self._user_id,
                is_admin=self._is_admin,
            )
        }

    async def scope_for(self, bucket: str) -> Scope:
        """`None` for the library bucket, else the connection uid."""
        if bucket == Settings.S3_COMPAT_BUCKET:
            return None
        connection = (await self._connection_buckets()).get(bucket)
        if connection is None:
            raise NoSuchBucket
        return connection.uid

    async def split_path(self, first: str, rest: str) -> tuple[str, str]:
        """`(bucket, key)` for `/{first}/{rest}`. A first segment that is not
        a bucket is the legacy bucket-less form: a whole-library key."""
        if first == Settings.S3_COMPAT_BUCKET:
            return first, rest
        if first in await self._connection_buckets():
            return first, rest
        return Settings.S3_COMPAT_BUCKET, f"{first}/{rest}"

    async def validate_bucket(self, bucket: str) -> None:
        await self.scope_for(bucket)

    async def head_bucket(self, bucket: str) -> Response:
        await self.validate_bucket(bucket)
        return Response(status_code=200)

    async def create_bucket(self, bucket: str) -> Response:
        """Idempotent for existing buckets. New buckets are made by adding a
        connection, not through S3."""
        await self.validate_bucket(bucket)
        return Response(status_code=200)

    async def _record_from_key(self, key: str, scope: Scope) -> MediaFileRecord:
        records = await self._media_files._files.list_visible(
            actor_user_id=self._user_id,
        )
        projected = file_keys(records)
        requested = decode_key(key)
        matches = [
            record
            for record in records
            if record.uid in projected
            and decode_key(projected[record.uid]) == requested
        ]
        if len(matches) != 1 or not _in_scope(matches[0], scope):
            raise NoSuchKey
        return matches[0]

    async def _upload_connection_id(self) -> str:
        connections = await self._connections.list(owner_id=self._user_id)
        for connection in connections:
            if not getattr(connection, "enabled", True):
                continue
            from apps.provider_connections.services import connection_usable_by

            if connection_usable_by(
                connection,
                actor_user_id=self._user_id,
                is_admin=self._is_admin,
            ):
                return connection.uid
        raise InvalidArgument("No storage provider configured")

    async def list_buckets(self) -> bytes:
        now = datetime.now(tz=UTC)
        buckets = [{"name": Settings.S3_COMPAT_BUCKET, "created": now}]
        buckets += [
            {"name": name, "created": getattr(connection, "created_at", None) or now}
            for name, connection in sorted((await self._connection_buckets()).items())
        ]
        return list_buckets_xml([
            {
                "name": bucket["name"],
                "creation_date": _timestamp(bucket["created"]),
            }
            for bucket in buckets
        ])

    async def list_objects_v2(
        self,
        *,
        bucket: str,
        prefix: str = "",
        delimiter: str = "",
        max_keys: int = 1000,
        continuation_token: str | None = None,
    ) -> bytes:
        scope = await self.scope_for(bucket)
        max_keys = min(max(max_keys, 1), MAX_KEYS_CAP)

        records = await self._media_files._files.list_visible(
            actor_user_id=self._user_id,
        )
        projected_files = file_keys(records)
        keyed = sorted(
            (projected_files[record.uid], record)
            for record in records
            if record.uid in projected_files and _in_scope(record, scope)
        )
        # Keys are projected over the whole library so a file has the same
        # key in every bucket; a connection bucket then shows only the
        # folders on the way to its own files, plus folders bound to it.
        projected_folders = folder_prefixes(records)
        if scope is not None:
            # Linear in keys x depth: a folder-by-key scan is quadratic and
            # stalls on real libraries (~8.6k folders x ~49k files).
            ancestors: set[str] = set()
            for key, _ in keyed:
                segments = key.split("/")[:-1]
                for depth in range(1, len(segments) + 1):
                    ancestors.add("/".join(segments[:depth]) + "/")
            bound = {
                record.uid
                for record in records
                if record.type == "folder" and record.provider_connection_id == scope
            }
            projected_folders = {
                uid: folder
                for uid, folder in projected_folders.items()
                if folder in ancestors or uid in bound
            }
        normalized_prefix = encode_key(decode_key(prefix))
        if prefix.endswith("/") and normalized_prefix:
            normalized_prefix = f"{normalized_prefix}/"

        start_after = continuation_token or ""
        objects: list[ObjectItem] = []
        common_prefixes: set[str] = set()
        is_truncated = False
        for key, record in keyed:
            if normalized_prefix and not key.startswith(normalized_prefix):
                continue
            if start_after and key <= start_after:
                continue
            if delimiter and delimiter in key[len(normalized_prefix):]:
                remainder = key[len(normalized_prefix):]
                common_prefixes.add(
                    normalized_prefix
                    + remainder.split(delimiter, 1)[0]
                    + delimiter,
                )
                continue
            if len(objects) >= max_keys:
                is_truncated = True
                break
            objects.append({
                "key": key,
                "size": record.size,
                "content_type": record.content_type,
                "last_modified": record.updated_at,
                "etag": _etag(record),
            })

        if delimiter:
            for folder_prefix in projected_folders.values():
                if not folder_prefix.startswith(normalized_prefix):
                    continue
                remainder = folder_prefix[len(normalized_prefix):]
                if delimiter not in remainder:
                    continue
                common_prefixes.add(
                    normalized_prefix
                    + remainder.split(delimiter, 1)[0]
                    + delimiter,
                )

        return list_objects_v2_xml(
            bucket=bucket,
            prefix=normalized_prefix,
            delimiter=delimiter,
            objects=objects,
            common_prefixes=sorted(common_prefixes),
            is_truncated=is_truncated,
            max_keys=max_keys,
            continuation_token=continuation_token,
            next_continuation_token=objects[-1]["key"]
            if is_truncated and objects else None,
        )

    async def get_object(
        self,
        *,
        bucket: str,
        key: str,
        range_header: str | None = None,
    ) -> Response | StreamingResponse:
        """GetObject: stream via the media layer (ACL + provider I/O)."""
        record = await self._record_from_key(key, await self.scope_for(bucket))
        try:
            record, stream = await self._media_files.read_content(
                record.uid, actor_user_id=self._user_id, range_header=range_header,
            )
        except MediaFileNotFoundError as exc:
            raise NoSuchKey from exc
        except MediaFilePermissionError as exc:
            raise AccessDenied from exc

        headers = _metadata_headers(record)
        bounds = _parse_range(range_header, record.size)
        if bounds is None:
            return StreamingResponse(stream, status_code=200, headers=headers)
        start, end = bounds
        headers["Content-Range"] = f"bytes {start}-{end}/{record.size}"
        headers["Content-Length"] = str(end - start + 1)
        return StreamingResponse(stream, status_code=206, headers=headers)

    async def head_object(self, *, bucket: str, key: str) -> Response:
        """HeadObject: the same metadata GetObject sends, empty body."""
        record = await self._record_from_key(key, await self.scope_for(bucket))
        return Response(status_code=200, headers=_metadata_headers(record))

    async def put_object(
        self,
        *,
        bucket: str,
        key: str,
        body: bytes,
        content_type: str | None = None,
    ) -> Response:
        scope = await self.scope_for(bucket)
        decoded = decode_key(key)
        segments = decoded.split("/") if decoded else []
        if not segments:
            raise InvalidArgument("Object key cannot be empty")

        records = await self._media_files._files.list_visible(
            actor_user_id=self._user_id,
        )
        projected_files = file_keys(records)
        existing_files = [
            record
            for record in records
            if record.uid in projected_files
            and decode_key(projected_files[record.uid]) == decoded
        ]
        if len(existing_files) > 1:
            raise InvalidArgument("Object key is ambiguous")
        if existing_files and not _in_scope(existing_files[0], scope):
            # Overwriting would silently move the file between providers.
            raise InvalidArgument(
                "Object key is stored on another connection; "
                "write it through that connection's bucket",
            )
        if existing_files and not key.endswith("/"):
            try:
                record = await self._media_files.replace_content(
                    existing_files[0].uid,
                    content=body,
                    content_type=content_type,
                    actor_user_id=self._user_id,
                )
            except MediaFilePermissionError as exc:
                raise AccessDenied from exc
            except MediaFileWriteFailedError as exc:
                raise InvalidArgument(str(exc.detail)) from exc
            return Response(status_code=200, headers={"ETag": _etag(record)})

        parent_id: str | None = None
        folder_segments = segments if key.endswith("/") else segments[:-1]
        try:
            for index, segment in enumerate(folder_segments, start=1):
                projected_folders = folder_prefixes(records)
                requested_prefix = "/".join(segments[:index])
                matches = [
                    record
                    for record in records
                    if record.uid in projected_folders
                    and decode_key(projected_folders[record.uid])
                    == requested_prefix
                ]
                if len(matches) > 1:
                    raise InvalidArgument("Folder path is ambiguous")
                if matches:
                    parent_id = matches[0].uid
                    continue
                folder = await self._media_files.create_folder(
                    name=segment,
                    parent_id=parent_id,
                    owner_id=self._user_id,
                    is_admin=self._is_admin,
                )
                records.append(folder)
                parent_id = folder.uid

            if key.endswith("/"):
                return Response(status_code=200, headers={"ETag": '"folder"'})

            record = await self._media_files.upload(
                provider_connection_id=scope or await self._upload_connection_id(),
                parent_id=parent_id,
                name=segments[-1],
                content=body,
                content_type=content_type,
                owner_id=self._user_id,
                is_admin=self._is_admin,
            )
        except (MediaFileWriteFailedError, MediaFileValidationError) as exc:
            raise InvalidArgument(str(exc.detail)) from exc
        except MediaFilePermissionError as exc:
            raise AccessDenied from exc
        return Response(
            status_code=200,
            headers={"ETag": _etag(record)},
        )

    async def _entry_for_delete(
        self, key: str, scope: Scope,
    ) -> MediaFileRecord | None:
        """Projected file, or folder when the key is a prefix. Missing → None."""
        decoded = decode_key(key)
        if not decoded:
            raise InvalidArgument("Object key cannot be empty")
        records = await self._media_files._files.list_visible(
            actor_user_id=self._user_id,
        )
        trailing_slash = key.endswith("/")
        if not trailing_slash:
            projected_files = file_keys(records)
            matches = [
                record
                for record in records
                if record.uid in projected_files
                and decode_key(projected_files[record.uid]) == decoded
            ]
            if len(matches) > 1:
                raise InvalidArgument("Object key is ambiguous")
            if matches:
                # Out of scope reads as missing: never delete across buckets.
                return matches[0] if _in_scope(matches[0], scope) else None
        projected_folders = folder_prefixes(records)
        folders = [
            record
            for record in records
            if record.uid in projected_folders
            and decode_key(projected_folders[record.uid]) == decoded
        ]
        if len(folders) > 1:
            raise InvalidArgument("Object key is ambiguous")
        if not folders:
            return None
        if scope is not None:
            # A library folder may hold files from several connections;
            # deleting it through one connection's bucket must not trash
            # the others'.
            prefix = projected_folders[folders[0].uid]
            projected_files = file_keys(records)
            for record in records:
                key_of = projected_files.get(record.uid)
                outside = key_of and not _in_scope(record, scope)
                if outside and key_of.startswith(prefix):
                    raise InvalidArgument(
                        "Folder holds files from another connection; "
                        "delete it through the library bucket",
                    )
        return folders[0]

    async def delete_object(self, *, bucket: str, key: str) -> Response:
        """DeleteObject: soft-delete the MediaFile (v1: bytes stay on the
        provider). Missing keys are 204, same as AWS."""
        record = await self._entry_for_delete(key, await self.scope_for(bucket))
        if record is None:
            return Response(status_code=204)
        try:
            await self._media_files.soft_delete(
                record.uid, actor_user_id=self._user_id,
            )
        except MediaFilePermissionError as exc:
            raise AccessDenied from exc
        except MediaFileNotFoundError:
            return Response(status_code=204)
        return Response(status_code=204)

    async def delete_objects(self, *, bucket: str, body: bytes) -> Response:
        """DeleteObjects (`POST ?delete=`): each key is DeleteObject."""
        await self.validate_bucket(bucket)
        try:
            keys, quiet = parse_delete_objects_body(body)
        except ValueError as exc:
            raise InvalidArgument(str(exc)) from exc
        deleted: list[str] = []
        errors: list[tuple[str, str, str]] = []
        for key in keys:
            try:
                response = await self.delete_object(bucket=bucket, key=key)
            except AccessDenied:
                errors.append((key, "AccessDenied", "Access Denied"))
                continue
            except InvalidArgument as exc:
                errors.append((key, "InvalidArgument", exc.message))
                continue
            if response.status_code == 204:
                deleted.append(key)
        return Response(
            content=delete_result_xml(
                deleted=deleted, errors=errors, quiet=quiet,
            ),
            media_type="application/xml",
        )


def _parse_range(
    range_header: str | None, size: int,
) -> tuple[int, int] | None:
    if not range_header:
        return None
    if not range_header.startswith("bytes="):
        raise InvalidArgument("Invalid Range header")
    start_str, _, end_str = range_header.removeprefix("bytes=").partition("-")
    try:
        start = int(start_str)
        end = int(end_str) if end_str else size - 1
    except ValueError as exc:
        raise InvalidArgument("Invalid Range header") from exc
    if start >= size or end >= size or start > end:
        raise InvalidArgument("Requested range is not satisfiable")
    return start, end
