import logging
import re
import tempfile
from datetime import datetime
from io import BytesIO
from typing import Never
from urllib.parse import quote

import httpx
from anyio import Path as AsyncPath
from fastapi import APIRouter, BackgroundTasks, Body, File, Request, UploadFile
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from fastapi_mongo_base.core.exceptions import BaseHTTPException
from fastapi_mongo_base.schemas import PaginatedResponse
from fastapi_mongo_base.utils import usso_routes
from usso import UserData
from usso.exceptions import PermissionDenied, USSOException
from usso.integrations.fastapi import USSOAuthentication

from server.config import Settings

from .file_manager import file_manager
from .models import FileMetaData
from .schemas import (
    FileMetaDataCreate,
    FileMetaDataSchema,
    FileMetaDataUpdate,
    FileStatus,
    MultiPartOut,
    PartUploadOut,
    PermissionEnum,
    VolumeOut,
)


def is_service_request(request: Request) -> bool:
    """Return True when the already-authenticated request used an API key."""
    return bool(request.headers.get("x-api-key"))


def resolve_upload_identity(
    request: Request,
    user: UserData,
    user_id: str | None,
    workspace_id: str | None,
) -> tuple[str, str | None]:
    """
    Resolve the id/workspace an upload is attributed to.

    Service (API-key) callers -- e.g. mirza-bot uploading on behalf of a
    specific Telegram user -- may set an explicit user_id/workspace_id.
    JWT end-users can never claim a different identity this way: always
    uploaded as themselves.
    """
    if is_service_request(request):
        return user_id or user.uid, workspace_id or getattr(user, "workspace_id", None)
    return user.uid, getattr(user, "workspace_id", None)


class FilesRouter(usso_routes.AbstractTenantUSSORouter):
    model = FileMetaData
    schema = FileMetaDataSchema
    resource = "files"

    def __init__(self) -> None:
        super().__init__(
            user_dependency=USSOAuthentication(raise_exception=False),
            prefix="/f",
            tags=["files"],
        )

    def config_schemas(self, schema: type, **kwargs: object) -> None:
        super().config_schemas(
            schema,
            list_item_schema=FileMetaDataSchema,
            retrieve_response_schema=None,
        )

    def config_routes(self) -> None:
        super().config_routes(statistics_route=True)
        self.router.add_api_route(
            "/{uid}/{path:path}",
            self.retrieve_item,
            methods=["GET"],
            # response_model=FileMetaDataSignedUrl,
        )
        self.router.add_api_route(
            "/{uid}",
            self.head_item,
            methods=["HEAD"],
            response_class=Response,
            responses={
                200: {
                    "description": "File metadata headers",
                    "headers": {
                        "Content-Type": {"description": "File MIME type"},
                        "Content-Length": {"description": "File size in bytes"},
                        "Last-Modified": {"description": "Last modification date"},
                    },
                },
                404: {"description": "File not found"},
                403: {"description": "Permission denied"},
            },
        )
        self.router.add_api_route(
            "/{uid}/{path:path}",
            self.head_item,
            methods=["HEAD"],
            response_class=Response,
            status_code=200,
            summary="Get file headers",
            description="Returns file metadata in HTTP headers with no response body",
            responses={
                200: {
                    "description": "File metadata headers",
                    "headers": {
                        "Content-Type": {"description": "File MIME type"},
                        "Content-Length": {"description": "File size in bytes"},
                        "Last-Modified": {"description": "Last modification date"},
                    },
                },
                404: {"description": "File not found"},
                403: {"description": "Permission denied"},
            },
        )
        self.router.add_api_route(
            "/{uid}",
            self.change_item,
            methods=["PUT"],
            response_model=FileMetaDataSchema,
        )
        self.router.add_api_route(
            "/upload",
            self.upload_file,
            methods=["POST"],
            response_model=FileMetaDataSchema,
        )
        self.router.add_api_route(
            "/upload/base64",
            self.upload_file_base64,
            methods=["POST"],
            response_model=FileMetaDataSchema,
        )
        self.router.add_api_route(
            "/upload/url",
            self.upload_url,
            methods=["POST"],
            response_model=FileMetaDataSchema,
        )
        self.router.add_api_route(
            "/upload/multipart",
            self.start_multipart,
            methods=["POST"],
            response_model=MultiPartOut,
            include_in_schema=False,
        )
        self.router.add_api_route(
            "/upload/multipart/{upload_id:str}",
            self.upload_part,
            methods=["POST"],
            response_model=PartUploadOut,
            include_in_schema=False,
        )
        self.router.add_api_route(
            "/upload/multipart/{upload_id:str}/complete",
            self.finish_multipart,
            methods=["POST"],
            response_model=FileMetaDataSchema,
            include_in_schema=False,
        )

    async def list_items(
        self,
        request: Request,
        *,
        offset: int = 0,
        limit: int = 50,
        parent_id: str | None = None,
        filename: str | None = None,
        filehash: str | None = None,
        is_deleted: bool = False,
        is_directory: bool | None = None,
        user_id: str | None = None,
        content_type: str | None = None,
    ) -> PaginatedResponse[FileMetaDataSchema]:
        user: UserData | None = await self.get_user(request)

        params = dict(request.query_params)
        root_permission = False
        workspace_id = None
        if not user:
            raise BaseHTTPException(
                status_code=401, error="unauthorized", detail="Unauthorized"
            )
        # TODO: check if user is root
        elif await self.authorize(action="read", user=user, raise_exception=False):
            user_id = user.uid
            workspace_id = getattr(user, "workspace_id", None)
            root_permission = params.get("root_permission", False)

        params.pop("user_id", None)
        params.pop("root_permission", None)
        params.pop("offset", None)
        params.pop("limit", None)
        params.pop("is_deleted", None)
        params.pop("is_directory", None)

        return await self._list_items(
            request,
            user_id=user_id,
            workspace_id=workspace_id,
            root_permission=root_permission,
            offset=offset,
            limit=limit,
            is_deleted=is_deleted,
            is_directory=is_directory,
            **params,
        )

    async def statistics(
        self,
        request: Request,
    ) -> VolumeOut:
        user: UserData = await self.get_user(request)
        if not user:
            raise BaseHTTPException(
                status_code=401, error="unauthorized", detail="Unauthorized"
            )

        volume_data = await FileMetaData.get_volume(user.uid)
        return VolumeOut(**volume_data)

    async def get_file(
        self,
        request: Request,
        uid: str,
        user_id: str | None = None,
        **kwargs: object,
    ) -> FileMetaData:
        try:
            user: UserData = await self.get_user(request)
            root_permission = await self.authorize(
                action="read", user=user, raise_exception=False
            )
            if root_permission:
                logging.info("%s, scopes=%s", root_permission, user.scopes)
        except USSOException:
            user = None
            root_permission = False

        user_id = None
        workspace_id = None
        if user:
            user_id = user.user_id
            workspace_id = getattr(user, "workspace_id", None)

        file: FileMetaData = await FileMetaData.get_item(
            user_id=user_id,
            workspace_id=workspace_id,
            uid=uid,
            root_permission=root_permission,
            **kwargs,
        )

        if file is None:
            raise BaseHTTPException(
                status_code=404, error="file_not_found", detail="File not found"
            )

        if file.user_permission(user_id).read or await self.authorize(
            action="read",
            user=user,
            filter_data=file.model_dump(),
            raise_exception=False,
        ):
            return file

        raise BaseHTTPException(
            status_code=404, error="file_not_found", detail="File not found"
        )

    async def retrieve_item(  # noqa: ANN201
        self,
        request: Request,
        uid: str,
        signed_url: bool = False,
        details: bool = False,
    ):
        file: FileMetaData = await self.get_file(request, uid)

        if details:
            return file

        if file.is_directory:
            return await self.list_items(
                request=request,
                parent_id=file.uid,
                offset=0,
                limit=Settings.page_max_limit,
            )

        file.access_at = datetime.now()
        await file.save()

        if signed_url:
            presigned_url = await file_manager.generate_presigned_url(file)
            return RedirectResponse(presigned_url)

        range_header = request.headers.get("Range")
        file_size = file.size

        if range_header:
            # Parse the Range header (e.g., "bytes=0-1023", "bytes=0-", "bytes=-1000")
            range_match = re.match(r"bytes=(\d+)-(\d*)", range_header)
            if not range_match:
                raise BaseHTTPException(
                    status_code=400,
                    error="invalid_range_header",
                    detail="Invalid Range header",
                )

            start_str, end_str = range_match.groups()

            # Handle different range formats
            if start_str and end_str:
                # bytes=0-1023
                start = int(start_str)
                end = int(end_str)
            elif start_str and not end_str:
                # bytes=1000- (from start to end of file)
                start = int(start_str)
                end = file_size - 1
            else:
                # Invalid range
                raise BaseHTTPException(
                    status_code=400,
                    error="invalid_range_header",
                    detail="Invalid Range header format",
                )

            # Validate range
            if start >= file_size or end >= file_size or start > end:
                return Response(
                    status_code=416,
                    headers={"Content-Range": f"bytes */{file_size}"},
                )

            # Ensure end doesn't exceed file size
            end = min(end, file_size - 1)

        else:
            start = None
            end = None

        stream = file_manager.stream_file(file, start=start, end=end, chunk_size=8192)

        # Prepare headers
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(file.filename)}'",
            "Content-Type": file.content_type,
        }

        # Handle range requests vs full file requests
        if range_header and start is not None and end is not None:
            # Partial content response
            content_length = end - start + 1
            status_code = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
            headers["Content-Length"] = str(content_length)
        else:
            # Full file response
            status_code = 200
            headers["Content-Length"] = str(file_size)

        return StreamingResponse(
            stream,
            status_code=status_code,
            headers=headers,
        )

    async def head_item(
        self,
        request: Request,
        uid: str,
        details: bool = False,
        signed_url: bool = False,
    ) -> Response:
        file: FileMetaData = await self.get_file(request, uid)
        headers = {
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(file.filename)}'",
            "Content-Length": str(file.size),
            "Content-Type": file.content_type,
            "Accept-Ranges": "bytes",
        }
        if details:
            headers.update({
                "Last-Modified": file.updated_at.strftime("%a, %d %b %Y %H:%M:%S GMT"),
                "ETag": f'"{file.filehash}"',
            })
        return Response(headers=headers)

    async def create_item(
        self,
        request: Request,
        data: FileMetaDataCreate,
    ) -> FileMetaData:
        user: UserData = await self.get_user(request)
        item = FileMetaData(
            user_id=user.uid,
            workspace_id=data.workspace_id or user.workspace_id,
            access_at=datetime.now(),
            **data.model_dump(
                exclude_none=True, exclude_unset=True, exclude={"workspace_id"}
            ),
        )
        await item.save()
        return item

    async def change_item(
        self,
        request: Request,
        uid: str,
        blocking: bool = False,
        file: UploadFile = File(...),  # noqa: B008
        overwrite: bool = False,
    ) -> FileMetaData:
        user: UserData = await self.get_user(request)
        item: FileMetaData = await self.get_file(request, uid)

        # Service (API-key) callers -- e.g. mirza-bot -- are trusted
        # backend services acting on behalf of end users. A file they
        # just uploaded on behalf of a Telegram user is attributed to
        # that user, not the shared key, so the normal per-file
        # permission check would otherwise lock the key out of files it
        # legitimately administers.
        if not is_service_request(request) and (
            not item.user_permission(user.uid).write
            or not await self.authorize(
                action="update",
                user=user,
                filter_data=item.model_dump(),
                raise_exception=False,
            )
        ):
            raise PermissionDenied(
                detail="You don't have permission to update this file",
            )

        if item.is_deleted:
            raise BaseHTTPException(
                status_code=400,
                error="file_deleted",
                detail="File is deleted",
            )

        meta_data = await file_manager.change_file(
            file=file, file_metadata=item, blocking=blocking, overwrite=overwrite
        )
        return meta_data

    async def update_item(
        self,
        request: Request,
        uid: str,
        update: FileMetaDataUpdate,
        is_deleted: bool = False,
    ) -> FileMetaData:
        user: UserData = await self.get_user(request)
        item: FileMetaData = await self.get_file(request, uid, is_deleted=is_deleted)

        # See change_item: service (API-key) callers -- e.g. mirza-bot
        # setting public_permission right after uploading on behalf of a
        # Telegram user -- must be able to manage files attributed to
        # someone other than the shared key itself.
        if not is_service_request(request) and (
            (
                item.user_permission(user.uid).permission
                < (
                    PermissionEnum.MANAGE
                    if update.need_manage_permissions
                    else PermissionEnum.WRITE
                )
            )
            or (
                not await self.authorize(
                    action=("manage" if update.need_manage_permissions else "update"),
                    user=user,
                    filter_data=item.model_dump(),
                    raise_exception=False,
                )
            )
        ):
            raise PermissionDenied(
                detail="You don't have permission to update this file",
            )

        if update.is_deleted is not None:
            if item.is_deleted and not update.is_deleted:
                await item.restore(user.uid)
            elif not item.is_deleted and update.is_deleted:
                await item.delete(user.uid)
        if update.filename:
            item.filename = update.filename
        if update.parent_id:
            existing: list[FileMetaData] = await FileMetaData.list_items(
                user_id=user.uid,
                uid=update.parent_id,
            )
            if not existing or not existing[0].is_directory:
                raise BaseHTTPException(
                    status_code=404,
                    error="parent_not_found",
                    detail="Parent directory not found",
                )
            item.parent_id = update.parent_id
        if update.public_permission:
            item.public_permission.permission = update.public_permission.permission
            item.public_permission.updated_at = datetime.now()
        for permission in update.permissions:
            await item.set_permission(permission)

        await item.save()
        return item

    async def delete_item(
        self, request: Request, uid: str, is_deleted: bool = False
    ) -> FileMetaData:
        user: UserData = await self.get_user(request)
        file: FileMetaData = await self.get_file(request, uid, is_deleted=is_deleted)

        # See change_item: service (API-key) callers must be able to
        # manage files attributed to someone other than the shared key.
        if not is_service_request(request) and (
            not file.user_permission(user.uid).delete
            or not await self.authorize(
                action="delete",
                user=user,
                filter_data=file.model_dump(),
                raise_exception=False,
            )
        ):
            raise PermissionDenied(
                detail="You don't have permission to delete this file",
            )

        await file.delete(user_id=user.uid)
        return file

    async def upload_file(
        self,
        request: Request,
        user_id: str | None = Body(default=None),
        workspace_id: str | None = Body(default=None),
        blocking: bool = False,
        file: UploadFile = File(..., description="The file to upload"),  # noqa: B008
        parent_id: str | None = Body(default=None),
        filename: str | None = Body(default=None),
    ) -> FileMetaDataSchema:
        user: UserData | None = await self.get_user(request)

        if user is None:
            raise USSOException(status_code=401, error="unauthorized")

        upload_user_id, upload_workspace_id = resolve_upload_identity(
            request, user, user_id, workspace_id
        )

        form_data = dict(await request.form())
        form_data.pop("user_id", None)
        form_data.pop("workspace_id", None)
        form_data.pop("parent_id", None)
        form_data.pop("filename", None)
        form_data.pop("blocking", None)
        file = form_data.pop("file", file)

        file_metadata = await file_manager.process_file(
            file=file,
            user_id=upload_user_id,
            workspace_id=upload_workspace_id,
            blocking=blocking,
            parent_id=parent_id,
            filename=filename,
            **form_data,
        )

        return file_metadata

    async def upload_file_base64(
        self,
        request: Request,
        # user: UserData = Depends(jwt_access_security),
        #
        user_id: str | None = Body(default=None),
        blocking: bool = False,
        file: str = Body(default=None),
        parent_id: str | None = Body(default=None),
        filename: str | None = Body(default=None),
        mime_type: str | None = Body(default=None),
    ) -> FileMetaDataSchema:
        import base64
        from io import BytesIO

        if mime_type is None and not file.startswith("data:"):
            raise BaseHTTPException(
                status_code=400,
                error="mime_type_required",
                detail="Mime type is required",
            )
        if mime_type is None:
            mime_type = file.split(";")[0].split(":")[1]
            file = file.split(";")[1]
        file_bytes = BytesIO(base64.b64decode(file))
        uploading_file = UploadFile(file=file_bytes, filename=filename or "file")
        return await self.upload_file(
            request, user_id, blocking, uploading_file, parent_id, filename
        )

    async def _download_to_temp_file(
        self,
        url: str,
        filename: str,
        max_memory_size: int = 50 * 1024 * 1024,  # 50MB
    ) -> tuple[UploadFile, bool]:
        """Download URL to temp file if large, otherwise keep in memory"""
        use_temp_file = False

        async with (
            httpx.AsyncClient() as client,
            client.stream("GET", url, follow_redirects=True) as response,
        ):
            response.raise_for_status()

            # Check content length to decide if we need temp file
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > max_memory_size:
                use_temp_file = True

            async def fill_temp_file(
                response: httpx.Response, initial_data: bytes | None = None
            ) -> UploadFile:
                with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                    temp_file_path = temp_file.name
                    if initial_data:
                        temp_file.write(initial_data)
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        temp_file.write(chunk)

                # Create UploadFile from temp file
                return UploadFile(
                    file=open(temp_file_path, "rb"),  # noqa ASYNC230
                    filename=filename,
                )

            if use_temp_file:
                # Use temporary file for large downloads
                upload_file = await fill_temp_file(response)
            else:
                # Keep in memory for small files
                file_data = b""
                total_size = 0
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    file_data += chunk
                    total_size += len(chunk)
                    # Switch to temp file if it grows too large
                    if total_size > max_memory_size:
                        use_temp_file = True
                        upload_file = await fill_temp_file(response, file_data)
                        break
                else:
                    # File was small enough for memory
                    upload_file = UploadFile(file=BytesIO(file_data), filename=filename)

        return upload_file, use_temp_file

    async def _cleanup_temp_file(
        self, upload_file: UploadFile, temp_file_used: bool
    ) -> None:
        """Clean up temporary file if it was used"""
        if not temp_file_used or not hasattr(upload_file.file, "name"):
            # no temp file or no name, upload_file created from BytesIO in memory
            return

        try:
            upload_file.file.close()
            AsyncPath(upload_file.file.name).unlink(missing_ok=True)
        except Exception as e:
            logging.warning("Failed to cleanup temp file: %s", e)

    async def _background_upload_task(
        self,
        url: str,
        user_id: str | None,
        parent_id: str | None,
        filename: str | None,
        file_metadata: FileMetaData,
    ) -> None:
        """Background task to complete file upload processing"""
        try:
            upload_file, temp_file_used = await self._download_to_temp_file(
                url, filename or url.split("/")[-1]
            )

            # Process the file
            completed_metadata = await file_manager.process_file(
                file=upload_file,
                user_id=user_id,
                blocking=True,  # Background processing is always blocking
                parent_id=parent_id,
                filename=filename,
            )

            # Update the original metadata with completed info
            file_metadata.key = completed_metadata.key
            # file_metadata.filename = completed_metadata.filename
            file_metadata.filehash = completed_metadata.filehash
            file_metadata.size = completed_metadata.size
            file_metadata.content_type = completed_metadata.content_type
            file_metadata.status = FileStatus.completed
            await file_metadata.save()

            completed_metadata.is_deleted = True
            await completed_metadata.delete(user_id=completed_metadata.user_id)

        except Exception as e:
            logging.exception("Background upload failed")
            file_metadata.status = FileStatus.failed
            file_metadata.error = str(e)
            await file_metadata.save()
        finally:
            await self._cleanup_temp_file(upload_file, temp_file_used)

    async def upload_url(
        self,
        request: Request,
        background_task: BackgroundTasks,
        url: str = Body(),
        user_id: str | None = Body(default=None),
        blocking: bool = False,
        parent_id: str | None = Body(default=None),
        filename: str | None = Body(default=None),
    ) -> FileMetaDataSchema:
        user: UserData = await self.get_user(request)

        if user is None:
            raise USSOException(status_code=401, error="unauthorized")

        if not user_id:
            user_id = user.uid

        final_filename = filename or url.split("/")[-1]

        if blocking:
            # Blocking mode: download and process completely
            upload_file, temp_file_used = await self._download_to_temp_file(
                url, final_filename
            )

            logging.info("uploading file: %s", upload_file.size)

            try:
                result = await self.upload_file(
                    request=request,
                    user_id=user_id,
                    blocking=True,
                    file=upload_file,
                    parent_id=parent_id,
                    filename=filename,
                )
                return result
            finally:
                await self._cleanup_temp_file(upload_file, temp_file_used)
        else:
            # Non-blocking mode: create placeholder and process in background
            # Create initial file metadata
            file_metadata = FileMetaData(
                user_id=user_id,
                filename=final_filename,
                parent_id=parent_id,
                # size=0,
                content_type="application/octet-stream",
                status="processing",
                # access_at=datetime.now(),
            )
            await file_metadata.save()

            # Start background task
            background_task.add_task(
                self._background_upload_task,
                url,
                user_id,
                parent_id,
                filename,
                file_metadata,
            )
            return FileMetaDataSchema.model_validate(file_metadata)

    async def start_multipart(
        self,
        request: Request,
        parent_id: str | None = Body(default=None),
        filename: str | None = Body(default=None),
        filehash: str = Body(),
    ) -> Never:
        user: UserData = await self.get_user(request)
        if user is None:
            raise USSOException(status_code=401, error="unauthorized")

        raise NotImplementedError("Multipart upload is not implemented yet")

    async def upload_part(
        self,
        request: Request,
        upload_id: str,
        part: UploadFile = File(..., description="The part to upload"),  # noqa: B008
        part_number: int = Body(),
        blocking: bool = False,
    ) -> Never:
        user: UserData = await self.get_user(request)
        if user is None:
            raise USSOException(status_code=401, error="unauthorized")

        raise NotImplementedError("Multipart upload is not implemented yet")

    async def finish_multipart(
        self,
        request: Request,
        upload_id: str,
    ) -> Never:
        user: UserData = await self.get_user(request)
        if user is None:
            raise USSOException(status_code=401, error="unauthorized")

        raise NotImplementedError("Multipart upload is not implemented yet")


router = FilesRouter().router
download_router = APIRouter(prefix="/d", tags=["files"])


@download_router.get(
    "/{uid:uuid}/{path:path}",
    include_in_schema=False,
    response_class=RedirectResponse | StreamingResponse,
)
@download_router.get(
    "/{uid:uuid}",
    include_in_schema=False,
    response_class=RedirectResponse | StreamingResponse,
)
async def download_file_endpoint(
    request: Request,
    uid: str,
    signed_url: bool = False,
) -> object:  # -> StreamingResponse | RedirectResponse:
    file = await FilesRouter().get_file(request, uid)

    if file.is_directory:
        raise BaseHTTPException(
            status_code=400,
            error="directory_is_not_downloadable",
            detail="Directory is not downloadable",
        )

    file.access_at = datetime.now()
    await file.save()

    if signed_url:
        presigned_url = await file_manager.generate_presigned_url(file)

        return RedirectResponse(presigned_url)

    return StreamingResponse(
        file_manager.stream_file(file),
        media_type=file.content_type,
        headers={
            "Content-Disposition": (
                f"attachment; filename*=UTF-8''{quote(file.filename)}"
            ),
            "Content-length": str(file.size),
        },
    )
