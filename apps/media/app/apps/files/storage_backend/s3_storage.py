"""S3 storage backend implementation."""

import asyncio
import logging
from collections.abc import AsyncGenerator, Coroutine
from contextlib import asynccontextmanager
from datetime import datetime
from io import BytesIO

import aioboto3
from aiobotocore.client import AioBaseClient
from aiobotocore.response import StreamingBody
from botocore.config import Config
from botocore.exceptions import ClientError
from fastapi_mongo_base.core.exceptions import BaseHTTPException
from pydantic import BaseModel

from apps.files.storage_backend import StorageBackend
from server.config import Settings


class S3Config(BaseModel):
    endpoint: str = Settings.S3_ENDPOINT
    access_key: str = Settings.S3_ACCESS_KEY
    secret_key: str = Settings.S3_SECRET_KEY
    region: str | None = Settings.S3_REGION
    bucket_name: str = Settings.S3_BUCKET_NAME
    domain: str | None = Settings.S3_DOMAIN


class S3StorageBackend(StorageBackend):
    """S3 storage backend implementation."""

    supported_backend = "s3"

    def __init__(
        self, config: dict[str, object] | None = None, **kwargs: object
    ) -> None:
        """
        Initialize S3 storage backend.

        Args:
            config: S3 configuration containing:
                - endpoint_url: S3 endpoint URL
                - aws_access_key_id: AWS access key ID
                - aws_secret_access_key: AWS secret access key
                - region_name: AWS region name
                - bucket_name: S3 bucket name
                - domain: S3 domain for public URLs
        """
        self.config: S3Config = S3Config.model_validate(config or {})
        self._session: aioboto3.Session | None = None

    @property
    def bucket_name(self) -> str:
        """Get S3 bucket name."""
        return self.config.bucket_name

    @property
    def botocore_config(self) -> Config:
        return Config(
            s3={
                "addressing_style": "path",
                "signature_type": Settings.S3_SIGNATURE_TYPE,
            },
            signature_version="s3",
            retries={"max_attempts": 3},
        )

    @property
    def client_kwargs(self) -> dict[str, str]:
        """Get S3 client configuration parameters."""
        return {
            "service_name": "s3",
            "endpoint_url": self.config.endpoint,
            "region_name": self.config.region,
            "config": self.botocore_config,
        }

    @property
    def session(self) -> aioboto3.Session:
        """Get cached S3 session."""
        if not self._session:
            self._session = aioboto3.Session(
                aws_access_key_id=self.config.access_key,
                aws_secret_access_key=self.config.secret_key,
                region_name=self.config.region,
            )
        return self._session

    @asynccontextmanager
    async def get_client(self) -> AsyncGenerator[AioBaseClient]:
        """Get S3 client."""
        async with self.session.client(**self.client_kwargs) as s3_client:
            yield s3_client

    async def _check_session_health(self) -> bool:
        """Check if the session is working using a lightweight head_bucket operation."""
        try:
            async with self.get_client() as s3_client:
                await s3_client.head_bucket(Bucket=self.bucket_name)
        except Exception:
            logging.exception("Session health check failed")
            self._session = None  # Reset session if check fails
            return False
        else:
            return True

    async def upload_file(
        self,
        file_bytes: BytesIO,
        key: str,
        content_type: str | None = None,
        metadata: dict[str, object] | None = None,
        **kwargs: object,
    ) -> dict[str, object]:
        """Upload file to S3."""

        # Determine upload method based on file size
        file_size = len(file_bytes.getvalue())

        async with self.get_client() as s3_client:
            if file_size > 100 * 1024 * 1024:  # 200MB
                await self._upload_multipart(s3_client, file_bytes, key, content_type)
            else:
                extra_args = {}
                if content_type:
                    extra_args["ContentType"] = content_type
                if metadata:
                    extra_args["Metadata"] = metadata

                await s3_client.upload_fileobj(
                    file_bytes, Bucket=self.bucket_name, Key=key, ExtraArgs=extra_args
                )

        return {
            "key": key,
            "bucket": self.bucket_name,
            "size": file_size,
            "url": f"{self.config.domain}/{key}" if self.config.domain else None,
        }

    async def _upload_multipart(
        self,
        s3_client: AioBaseClient,
        file_bytes: BytesIO,
        key: str,
        content_type: str | None = None,
        chunk_size: int = 32 * 1024 * 1024,  # 100MB
    ) -> None:
        """Upload large file using multipart upload."""
        # Create multipart upload
        extra_args = {}
        if content_type:
            extra_args["ContentType"] = content_type

        response = await s3_client.create_multipart_upload(
            Bucket=self.bucket_name, Key=key, **extra_args
        )
        upload_id = response["UploadId"]

        file_bytes.seek(0)
        parts = []
        part_number = 1

        try:
            while True:
                chunk = file_bytes.read(chunk_size)
                if not chunk:
                    break

                part_response = await s3_client.upload_part(
                    Bucket=self.bucket_name,
                    Key=key,
                    PartNumber=part_number,
                    UploadId=upload_id,
                    Body=chunk,
                )
                parts.append({"PartNumber": part_number, "ETag": part_response["ETag"]})
                part_number += 1

            # Complete multipart upload
            await s3_client.complete_multipart_upload(
                Bucket=self.bucket_name,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )
        except Exception:
            logging.exception("Multipart upload failed")
            # Abort multipart upload on error
            await s3_client.abort_multipart_upload(
                Bucket=self.bucket_name, Key=key, UploadId=upload_id
            )
            raise

    async def download_file(self, key: str, **kwargs: object) -> BytesIO:
        """Download file from S3."""
        async with self.get_client() as s3_client:
            try:
                response: dict[
                    str, StreamingBody | datetime | dict | int | str
                ] = await s3_client.get_object(Bucket=self.bucket_name, Key=key)
                file_bytes = BytesIO(await response["Body"].read())
                file_bytes.seek(0)
            except ClientError as e:
                raise BaseHTTPException(
                    status_code=404,
                    error="file_not_found",
                    detail="File not found",
                ) from e

            return file_bytes

    async def stream_file(
        self,
        key: str,
        *,
        start: int | None = None,
        end: int | None = None,
        chunk_size: int = 8192,
        **kwargs: object,
    ) -> AsyncGenerator[bytes]:
        """Stream file from S3."""
        async with self.get_client() as s3_client:
            get_object_kwargs = {"Bucket": self.bucket_name, "Key": key}
            if start is not None and end is not None:
                get_object_kwargs["Range"] = f"bytes={start}-{end}"

            body: StreamingBody | None = None

            try:
                response: dict[
                    str, StreamingBody | datetime | dict | int | str
                ] = await s3_client.get_object(**get_object_kwargs)
                body = response["Body"]

                async for chunk in body.iter_chunks(chunk_size=chunk_size):
                    yield chunk
            except ClientError as e:
                raise BaseHTTPException(
                    status_code=404,
                    error="file_not_found",
                    detail="File not found",
                ) from e
            finally:
                if body and hasattr(body, "close"):
                    if asyncio.iscoroutinefunction(body.close):
                        await body.close()
                    else:
                        body.close()

    async def delete_file(self, key: str, **kwargs: object) -> bool:
        """Delete file from S3."""
        async with self.get_client() as s3_client:
            try:
                await s3_client.delete_object(Bucket=self.bucket_name, Key=key)
            except Exception:
                logging.exception("Failed to delete file")
                return False

        return True

    async def file_exists(self, key: str, **kwargs: object) -> bool:
        """Check if file exists in S3."""
        async with self.get_client() as s3_client:
            try:
                await s3_client.head_object(Bucket=self.bucket_name, Key=key)
            except ClientError:
                return False
        return True

    async def get_file_info(self, key: str, **kwargs: object) -> dict[str, object]:
        """Get file information from S3."""
        async with self.get_client() as s3_client:
            try:
                response: dict[str, datetime | int | str] = await s3_client.head_object(
                    Bucket=self.bucket_name, Key=key
                )
            except ClientError as e:
                raise BaseHTTPException(
                    status_code=404,
                    error="file_not_found",
                    detail="File not found",
                ) from e

        return {
            "key": key,
            "size": response.get("ContentLength", 0),
            "content_type": response.get("ContentType"),
            "last_modified": response.get("LastModified"),
            "etag": response.get("ETag"),
            "metadata": response.get("Metadata", {}),
        }

    async def generate_presigned_url(
        self, key: str, expires_in: int = 3600, **kwargs: object
    ) -> str | None:
        """Generate presigned URL for S3 object."""
        async with self.get_client() as s3_client:
            try:
                response = await s3_client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": self.bucket_name, "Key": key},
                    ExpiresIn=expires_in,
                )
            except Exception:
                return None

        return response

    async def copy_file(
        self, source_key: str, destination_key: str, **kwargs: object
    ) -> bool:
        """Copy file within S3."""
        async with self.get_client() as s3_client:
            try:
                copy_source = {"Bucket": self.bucket_name, "Key": source_key}
                await s3_client.copy_object(
                    CopySource=copy_source,
                    Bucket=self.bucket_name,
                    Key=destination_key,
                )
            except Exception:
                logging.exception("Failed to copy file")
                return False
        return True

    async def move_file(
        self, source_key: str, destination_key: str, **kwargs: object
    ) -> bool:
        """Move file within S3."""
        # S3 doesn't have native move, so copy then delete
        if await self.copy_file(source_key, destination_key, **kwargs):
            return await self.delete_file(source_key, **kwargs)
        return False

    def get_public_url(self, key: str, **kwargs: object) -> str | None:
        """Get public URL for S3 object."""
        if self.config.domain:
            return f"{self.config.domain}/{key}"
        return None

    async def list_files(
        self, prefix: str = "", limit: int = 1000, **kwargs: object
    ) -> list[dict[str, object]]:
        """List files in S3."""
        async with self.get_client() as s3_client:
            try:
                response = await s3_client.list_objects_v2(
                    Bucket=self.bucket_name,
                    Prefix=prefix,
                    MaxKeys=limit,
                )

                files = [
                    {
                        "key": obj["Key"],
                        "size": obj["Size"],
                        "last_modified": obj["LastModified"],
                        "etag": obj["ETag"],
                    }
                    for obj in response.get("Contents", [])
                ]
            except Exception:
                logging.exception("Failed to list files")
                return []
        return files

    async def _delete_batch(
        self,
        s3_client: AioBaseClient,
        batch: list[dict[str, str]],
        sem: asyncio.Semaphore,
    ) -> None:
        """Delete a batch of objects with semaphore control."""
        async with sem:
            await s3_client.delete_objects(
                Bucket=self.bucket_name,
                Delete={"Objects": batch, "Quiet": True},
            )

    async def cleanup(self, max_parallel: int = 10) -> None:
        """
        Delete all files in the bucket in parallel.

        Args:
            max_parallel: Maximum number of concurrent delete operations (default: 10)
        """
        async with self.get_client() as s3_client:
            try:
                # Create semaphore to limit concurrent operations
                sem = asyncio.Semaphore(max_parallel)
                paginator = s3_client.get_paginator("list_objects_v2")

                async for page in paginator.paginate(Bucket=self.bucket_name):
                    if "Contents" not in page:
                        continue

                    delete_tasks: list[Coroutine] = []
                    # Split objects into batches of 1000 (S3 limit)
                    all_objects = [{"Key": obj["Key"]} for obj in page["Contents"]]
                    for i in range(0, len(all_objects), 1000):
                        batch = all_objects[i : i + 1000]
                        if batch:
                            # Create delete task for each batch with semaphore control
                            delete_tasks.append(
                                self._delete_batch(s3_client, batch, sem)
                            )

                    # Execute delete operations with controlled parallelism
                    if delete_tasks:
                        await asyncio.gather(*delete_tasks)

            except Exception:
                logging.exception("Failed to cleanup bucket")
