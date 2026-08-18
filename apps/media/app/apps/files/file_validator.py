"""File validation service for handling file type checking and size validation."""

import hashlib
from io import BytesIO
from typing import BinaryIO

import magic
from fastapi import UploadFile
from fastapi_mongo_base.core.exceptions import BaseHTTPException
from singleton import Singleton

from server.config import Settings


class FileValidator(metaclass=Singleton):
    """Service responsible for file validation and type checking."""

    size_limits: int = Settings.size_limits
    accepted_file_types: list[str] = Settings.accepted_file_types

    @classmethod
    def check_file_type(cls, file: BytesIO) -> str:
        """
        Check and validate file MIME type.

        Args:
            file: BytesIO object containing file data
            accepted_mimes: List of accepted MIME types

        Returns:
            str: Detected MIME type

        Raises:
            BaseHTTPException: If file type is not supported
        """

        file.seek(0)  # Reset the file pointer to the beginning

        # Initialize the magic MIME type detector
        mime_detector = magic.Magic(mime=True)

        # Detect MIME type from the buffer
        mime_type = mime_detector.from_buffer(
            file.read(2048)
        )  # Read the first 2048 bytes

        file.seek(0)  # Reset the file pointer to the beginning

        if cls.accepted_file_types and (
            not mime_type or mime_type not in cls.accepted_file_types
        ):
            raise BaseHTTPException(
                status_code=400, error="unsupported", detail="Unsupported file type"
            )
        return mime_type

    @classmethod
    def validate_file_size(cls, size: int) -> None:
        """
        Validate file size against configured limits.

        Args:
            size: File size in bytes

        Raises:
            BaseHTTPException: If file size exceeds limit
        """
        if cls.size_limits > 0 and size > cls.size_limits:
            raise BaseHTTPException(
                status_code=400,
                error="file_too_large",
                detail="File size exceeds the maximum allowed size",
            )

    @classmethod
    def validate_file_and_get_metadata(
        cls, file: BytesIO, **kwargs: object
    ) -> tuple[str, int]:
        """
        Validate file and return metadata.

        Args:
            file: BytesIO object containing file data
            **kwargs: Additional validation parameters

        Returns:
            tuple: (mime_type, file_size)

        Raises:
            BaseHTTPException: If validation fails
        """
        mime = cls.check_file_type(file)
        size = len(file.getvalue())
        cls.validate_file_size(size)

        # TODO: check for security (svg valid scripts)
        # TODO: convert if requested (ico, svg -> webp)

        return mime, size


class FileHashCalculator(metaclass=Singleton):
    """Service responsible for calculating file hashes."""

    @classmethod
    def _calculate_hash_from_stream(
        cls, stream: BinaryIO, chunk_size: int = 8192
    ) -> str:
        """
        Calculate SHA256 hash from any readable stream.

        Args:
            stream: Any readable stream (file, BytesIO, etc.)
            chunk_size: Size of chunks to read

        Returns:
            str: Hexadecimal hash string
        """
        file_hash = hashlib.sha256()
        while chunk := stream.read(chunk_size):
            file_hash.update(chunk)
        return file_hash.hexdigest()

    @classmethod
    def calculate_file_hash_from_upload(cls, file: UploadFile) -> str:
        """
        Calculate SHA256 hash from UploadFile.

        Args:
            file: FastAPI UploadFile object

        Returns:
            str: Hexadecimal hash string
        """
        file.file.seek(0)  # Ensure we start from the beginning of the file
        result = cls._calculate_hash_from_stream(file.file)
        file.file.seek(0)  # Reset file pointer to the beginning
        return result

    @classmethod
    def calculate_file_hash_from_bytes(cls, file_bytes: BytesIO) -> str:
        """
        Calculate SHA256 hash from BytesIO.

        Args:
            file_bytes: BytesIO object containing file data

        Returns:
            str: Hexadecimal hash string
        """
        file_bytes.seek(0)
        result = cls._calculate_hash_from_stream(file_bytes)
        file_bytes.seek(0)
        return result
