"""HTTP-facing errors for the MediaFile domain."""

from fastapi_mongo_base.core.exceptions import BaseHTTPException


class MediaFileNotFoundError(BaseHTTPException):
    def __init__(self, uid: str) -> None:
        super().__init__(
            status_code=404,
            error_code="media_file_not_found",
            detail=f"File '{uid}' not found",
            message="File not found",
        )


class MediaFilePermissionError(BaseHTTPException):
    """The actor can *see* the file but lacks the level an operation
    needs. A caller who can't even see it gets `MediaFileNotFoundError`
    instead -- a 403 would confirm the uid exists."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            status_code=403,
            error_code="media_file_permission_denied",
            detail=detail,
            message=detail,
        )


class MediaFileValidationError(BaseHTTPException):
    """A malformed request -- e.g. an unknown parent, a non-folder parent."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            status_code=422,
            error_code="invalid_media_file_request",
            detail=detail,
            message=detail,
        )


class MediaFileStateError(BaseHTTPException):
    """An operation invalid in the file's current state -- e.g.
    permanently deleting one that was never soft-deleted."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            status_code=409,
            error_code="invalid_media_file_state",
            detail=detail,
            message=detail,
        )


class MediaFileWriteFailedError(BaseHTTPException):
    """The provider plugin rejected or failed a write. The MediaFile row
    is never left pointing at content that was never written -- creation
    paths mark it `failed` before raising this; mirror paths raise before
    touching the library row at all."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            status_code=400,
            error_code="media_file_write_failed",
            detail=detail,
            message="Could not write to the storage provider",
        )


class MediaFileDeleteFailedError(BaseHTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(
            status_code=502,
            error_code="media_file_delete_failed",
            detail=detail,
            message="Could not delete from the storage provider",
        )
