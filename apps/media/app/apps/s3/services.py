"""S3 object operations projected from the visible MediaFile library tree."""

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
    delete_result_xml,
    list_buckets_xml,
    list_objects_v2_xml,
    parse_delete_objects_body,
)

MAX_KEYS_CAP = 1000


class ConnectionListProtocol(Protocol):
    """The slice of `ProviderConnectionRepository` PutObject needs."""

    async def list(self) -> list[Any]: ...


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


class S3ObjectService:
    """S3 operations over the key owner's visible slice of the library."""

    def __init__(
        self,
        *,
        user_id: str,
        media_files: MediaFileService,
        connections: ConnectionListProtocol,
    ) -> None:
        self._user_id = user_id
        self._media_files = media_files
        self._connections = connections

    def validate_bucket(self, bucket: str) -> None:
        if bucket != Settings.S3_COMPAT_BUCKET:
            raise NoSuchBucket

    async def head_bucket(self, bucket: str) -> Response:
        self.validate_bucket(bucket)
        return Response(status_code=200)

    async def create_bucket(self, bucket: str) -> Response:
        """Idempotent: the single configured bucket already exists."""
        self.validate_bucket(bucket)
        return Response(status_code=200)

    async def _record_from_key(self, key: str) -> MediaFileRecord:
        records = await self._media_files._files.list_visible(  # noqa: SLF001
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
        if len(matches) != 1:
            raise NoSuchKey
        return matches[0]

    async def _upload_connection_id(self) -> str:
        connections = await self._connections.list()
        for connection in connections:
            if getattr(connection, "enabled", True):
                return connection.uid
        raise InvalidArgument("No storage provider configured")

    async def list_buckets(self) -> bytes:
        creation_date = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        return list_buckets_xml([
            {
                "name": Settings.S3_COMPAT_BUCKET,
                "creation_date": creation_date,
            },
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
        self.validate_bucket(bucket)
        max_keys = min(max(max_keys, 1), MAX_KEYS_CAP)

        records = await self._media_files._files.list_visible(  # noqa: SLF001
            actor_user_id=self._user_id,
        )
        projected_files = file_keys(records)
        projected_folders = folder_prefixes(records)
        keyed = sorted(
            (projected_files[record.uid], record)
            for record in records
            if record.uid in projected_files
        )
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
        key: str,
        range_header: str | None = None,
    ) -> Response | StreamingResponse:
        """GetObject: stream via the media layer (ACL + provider I/O)."""
        record = await self._record_from_key(key)
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

    async def head_object(self, *, key: str) -> Response:
        """HeadObject: the same metadata GetObject sends, empty body."""
        record = await self._record_from_key(key)
        return Response(status_code=200, headers=_metadata_headers(record))

    async def put_object(
        self,
        *,
        key: str,
        body: bytes,
        content_type: str | None = None,
    ) -> Response:
        decoded = decode_key(key)
        segments = decoded.split("/") if decoded else []
        if not segments:
            raise InvalidArgument("Object key cannot be empty")

        records = await self._media_files._files.list_visible(  # noqa: SLF001
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
                )
                records.append(folder)
                parent_id = folder.uid

            if key.endswith("/"):
                return Response(status_code=200, headers={"ETag": '"folder"'})

            record = await self._media_files.upload(
                provider_connection_id=await self._upload_connection_id(),
                parent_id=parent_id,
                name=segments[-1],
                content=body,
                content_type=content_type,
                owner_id=self._user_id,
            )
        except (MediaFileWriteFailedError, MediaFileValidationError) as exc:
            raise InvalidArgument(str(exc.detail)) from exc
        except MediaFilePermissionError as exc:
            raise AccessDenied from exc
        return Response(
            status_code=200,
            headers={"ETag": _etag(record)},
        )

    async def _entry_for_delete(self, key: str) -> MediaFileRecord | None:
        """Projected file, or folder when the key is a prefix. Missing → None."""
        decoded = decode_key(key)
        if not decoded:
            raise InvalidArgument("Object key cannot be empty")
        records = await self._media_files._files.list_visible(  # noqa: SLF001
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
                return matches[0]
        projected_folders = folder_prefixes(records)
        folders = [
            record
            for record in records
            if record.uid in projected_folders
            and decode_key(projected_folders[record.uid]) == decoded
        ]
        if len(folders) > 1:
            raise InvalidArgument("Object key is ambiguous")
        return folders[0] if folders else None

    async def delete_object(self, *, key: str) -> Response:
        """DeleteObject: soft-delete the MediaFile (v1: bytes stay on the
        provider). Missing keys are 204, same as AWS."""
        try:
            record = await self._entry_for_delete(key)
        except InvalidArgument:
            raise
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
        self.validate_bucket(bucket)
        try:
            keys, quiet = parse_delete_objects_body(body)
        except ValueError as exc:
            raise InvalidArgument(str(exc)) from exc
        deleted: list[str] = []
        errors: list[tuple[str, str, str]] = []
        for key in keys:
            try:
                response = await self.delete_object(key=key)
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
