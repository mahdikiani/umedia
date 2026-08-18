"""Unified file manager using pluggable storage backends."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from io import BytesIO
from pathlib import Path

from fastapi import UploadFile
from fastapi_mongo_base.core.exceptions import BaseHTTPException
from fastapi_mongo_base.utils import basic
from singleton import Singleton

from server.config import Settings

from .file_validator import FileHashCalculator, FileValidator
from .models import FileMetaData, ObjectMetaData
from .schemas import FileMetaDataHistory, PermissionEnum, PermissionSchema
from .storage_backend import StorageBackend


class FileManager(metaclass=Singleton):
    """Unified file manager that uses pluggable storage backends."""

    def __init__(self, storage_backend_name: str | None = None) -> None:
        """
        Initialize file manager with storage backend.

        Args:
            storage_backend_name: Name of storage backend to use, if None uses default
        """
        self.storage_backend_name = storage_backend_name or Settings.STORAGE_BACKEND
        self._storage_backend = None

    @property
    def storage_backend(self) -> StorageBackend:
        """Get storage backend instance."""
        if self._storage_backend is None:
            self._storage_backend = StorageBackend.create_storage_backend(
                backend_type=self.storage_backend_name
            )
        return self._storage_backend

    async def _get_filepath(
        self,
        *,
        user_id: str,
        filename: str | None = None,
        parent_id: str | None = None,
        file_filename: str | None = None,
    ) -> tuple[str, str]:
        """
        Get filepath and filename from filename or file.

        Args:
            user_id: User ID
            filename: Filename
            parent_id: Parent directory ID
            file_filename: File filename
        """
        if filename:
            filepath = filename
            if "/" in filename:
                if filename[-1] == "/":
                    raise BaseHTTPException(
                        status_code=400,
                        error_code="invalid_filename",
                        detail="Filename cannot end with a slash",
                    )
                parent_id, filename = await FileMetaData.get_path(
                    filepath=filepath,
                    user_id=user_id,
                    parent_id=parent_id,
                    create=True,
                )
            return filepath, filename

        return file_filename, file_filename

    async def process_file(
        self,
        file: UploadFile,
        *,
        user_id: str,
        workspace_id: str | None = None,
        parent_id: str | None = None,
        filename: str | None = None,
        blocking: bool = False,
        **kwargs: object,
    ) -> FileMetaData:
        """
        Process uploaded file and store in configured backend.

        Args:
            file: Uploaded file
            user_id: User ID
            workspace_id: Workspace ID, when the upload should be billed/
                visible to the whole workspace instead of just the user
            parent_id: Parent directory ID
            filename: Custom filename
            blocking: Whether to wait for upload completion
            **kwargs: Additional parameters

        Returns:
            FileMetaData: File metadata record
        """
        file_bytes = BytesIO(await file.read())

        filepath, filename = await self._get_filepath(
            user_id=user_id,
            filename=filename,
            parent_id=parent_id,
            file_filename=file.filename,
        )
        file_bytes.name = filename

        file_metadata = {"file_dir": str(Path(filepath).parent) + "/"}

        # Validate file and get metadata
        mime, size = FileValidator.validate_file_and_get_metadata(file_bytes)

        # Calculate file hash
        filehash = FileHashCalculator.calculate_file_hash_from_bytes(file_bytes)
        filename = file_bytes.name

        # Check if file already exists
        existing: list[FileMetaData] = await FileMetaData.list_items(
            filehash=filehash, user_id=user_id
        )
        for existed in existing:
            if existed.parent_id == parent_id:
                return existed

        # Generate storage key
        specific_dir = f"{Settings.specific_dir}/" if Settings.specific_dir else ""
        file_key = f"{Settings.project_name}/{specific_dir}{user_id}/{filehash}"

        # Upload to storage backend
        upload_task = asyncio.create_task(
            self._manage_upload_to_storage(
                file_bytes,
                file_key,
                size=size,
                filehash=filehash,
                content_type=mime,
                **kwargs,
            )
        )
        if blocking:
            await upload_task

        # Set up permissions
        public_permission = PermissionSchema(
            permission=(
                PermissionEnum.READ
                if Settings().pubilc_access_type
                else PermissionEnum.NONE
            )
        )
        if kwargs.get("public_permission"):
            try:
                public_permission = PermissionSchema.model_validate_json(
                    kwargs.get("public_permission")
                )
            except Exception as e:
                logging.warning(
                    "Invalid public_permission: %s\n%s",
                    e,
                    kwargs.get("public_permission"),
                )

        # Create metadata record
        return await FileMetaData(
            user_id=user_id,
            workspace_id=workspace_id,
            meta_data=file_metadata,
            filehash=filehash,
            filename=filename,
            key=file_key,  # Keep field name for compatibility
            content_type=mime,
            size=size,
            parent_id=parent_id,
            public_permission=public_permission,
        ).save()

    async def change_file(
        self,
        file_metadata: FileMetaData,
        file: UploadFile,
        blocking: bool = False,
        overwrite: bool = False,
        **kwargs: object,
    ) -> FileMetaData:
        """
        Change existing file content.

        Args:
            file_metadata: Existing file metadata
            file: New file content
            blocking: Whether to wait for upload completion
            overwrite: Whether to overwrite or keep history
            **kwargs: Additional parameters

        Returns:
            FileMetaData: Updated file metadata
        """
        file_bytes = BytesIO(await file.read())
        filehash = FileHashCalculator.calculate_file_hash_from_bytes(file_bytes)
        mime, size = FileValidator.validate_file_and_get_metadata(file_bytes)
        _, filename = await self._get_filepath(
            user_id=file_metadata.user_id,
            # filename=file_metadata.filename,
            parent_id=file_metadata.parent_id,
            file_filename=file.filename,
        )
        file_bytes.name = filename

        # Check if file with this hash already exists
        existing = await FileMetaData.list_items(
            user_id=file_metadata.user_id,
            filehash=filehash,
        )
        if existing:
            for existed in existing:
                if existed.parent_id == file_metadata.parent_id:
                    return existed

        # Generate new storage key
        file_key = f"{Settings().project_name}/{filehash}"

        # Upload new file
        upload_task = asyncio.create_task(
            self._manage_upload_to_storage(
                file_bytes,
                file_key,
                size=size,
                filehash=filehash,
                content_type=mime,
            )
        )
        if blocking:
            await upload_task

        # Handle old file
        if overwrite:
            await self.storage_backend.delete_file(file_metadata.key)
        else:
            file_metadata.history.append(
                FileMetaDataHistory.model_validate(file_metadata)
            )

        # Update metadata
        file_metadata.key = file_key
        file_metadata.size = size
        file_metadata.content_type = mime
        file_metadata.filehash = filehash
        file_metadata.filename = file_bytes.name

        return await file_metadata.save()

    @basic.retry_execution(attempts=3, delay=1)
    async def _manage_upload_to_storage(
        self,
        file_bytes: BytesIO,
        file_key: str,
        filehash: str,
        content_type: str,
        size: int,
        **kwargs: object,
    ) -> ObjectMetaData:
        """
        Manage upload to storage backend with metadata tracking.

        Args:
            file_bytes: File data
            file_key: Storage key
            filehash: File hash
            content_type: MIME type
            size: File size
            **kwargs: Additional parameters

        Returns:
            ObjectMetaData: Object metadata record
        """
        # Check if object already exists
        objects = await ObjectMetaData.find({"key": file_key}).to_list()
        if objects:
            return objects[0]

        # Upload to storage backend
        upload_result = await self.storage_backend.upload_file(
            file_bytes=file_bytes, key=file_key, content_type=content_type, **kwargs
        )

        # Verify upload
        if not await self.storage_backend.file_exists(file_key):
            raise BaseHTTPException(
                status_code=500,
                error_code="upload_failed",
                detail="Upload failed - file not found after upload",
            )

        # Create object metadata
        obj = await ObjectMetaData(
            key=file_key,
            size=size,
            object_hash=filehash,
            content_type=content_type,
            url=upload_result.get("url"),
        ).save()
        file_bytes.close()
        return obj

    async def download_file(self, file_metadata: FileMetaData) -> BytesIO:
        """
        Download file from storage backend.

        Args:
            file_metadata: File metadata

        Returns:
            BytesIO: File content
        """
        return await self.storage_backend.download_file(file_metadata.key)

    async def stream_file(
        self, file_metadata: FileMetaData, **kwargs: object
    ) -> AsyncGenerator[bytes]:
        """
        Stream file from storage backend.

        Args:
            file_metadata: File metadata
            **kwargs: Additional parameters (start, end, etc.)

        Yields:
            bytes: File content chunks
        """
        async for chunk in self.storage_backend.stream_file(
            file_metadata.key, **kwargs
        ):
            yield chunk

    async def delete_file(self, file_metadata: FileMetaData | str) -> bool:
        """
        Delete file from storage backend.

        Args:
            file_metadata: File metadata

        Returns:
            bool: True if deletion was successful
        """

        if isinstance(file_metadata, FileMetaData):
            key = file_metadata.key
        else:
            key = file_metadata
        return await self.storage_backend.delete_file(key)

    async def generate_presigned_url(
        self, file_metadata: FileMetaData, expires_in: int = 3600
    ) -> str | None:
        """
        Generate presigned URL for file access.

        Args:
            file_metadata: File metadata
            expires_in: URL expiration time in seconds

        Returns:
            str | None: Presigned URL or None if not supported
        """
        return await self.storage_backend.generate_presigned_url(
            file_metadata.key, expires_in=expires_in
        )


# Global file manager instance
file_manager = FileManager(storage_backend_name=Settings.STORAGE_BACKEND)
