"""HTTP-facing errors for the `Resource` domain."""

from fastapi_mongo_base.core.exceptions import BaseHTTPException


class ResourceNotFoundError(BaseHTTPException):
    def __init__(self, uid: str) -> None:
        super().__init__(
            status_code=404,
            error_code="resource_not_found",
            detail=f"Resource '{uid}' not found",
            message="Resource not found",
        )


class ResourcePermissionError(BaseHTTPException):
    """Raised when the actor can *see* the resource but lacks the level an
    operation needs (e.g. READ share trying to write). A caller who can't
    even see it gets `ResourceNotFoundError` instead -- a 403 would
    confirm the uid exists."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            status_code=403,
            error_code="resource_permission_denied",
            detail=detail,
            message=detail,
        )


class ResourceValidationError(BaseHTTPException):
    """Raised for a malformed request -- e.g. an unknown parent."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            status_code=422,
            error_code="invalid_resource_request",
            detail=detail,
            message=detail,
        )


class ResourceStateError(BaseHTTPException):
    """Raised for an operation invalid in the resource's current state --
    e.g. hard-deleting one that was never soft-deleted."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            status_code=409,
            error_code="invalid_resource_state",
            detail=detail,
            message=detail,
        )


class ResourceWriteFailedError(BaseHTTPException):
    """Raised when the provider plugin rejects or fails a write.

    The `Resource` row is never left pointing at content that was never
    actually written -- it's already been marked `status: failed` with
    this error's detail by the time this is raised, see
    docs/02-architecture.md "Correctness & consistency" #2.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(
            status_code=400,
            error_code="resource_write_failed",
            detail=detail,
            message="Could not write to the storage provider",
        )
