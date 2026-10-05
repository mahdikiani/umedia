"""S3-compatible API routes (`/s3/...` under the versioned prefix).

Routes only parse HTTP: the `s3_auth` dependency verifies the SigV4
signature (header or presigned-query form) and resolves the key owner;
every object decision lives in `S3ObjectService` / `MediaFileService`.

URL layout (single-bucket, path-style):

- `GET /s3` -- ListBuckets: the library bucket plus one per connection
- `GET /s3/{bucket}` -- ListObjectsV2
- `GET /s3/{bucket}/{library-path}` -- GetObject
- Legacy (still served): `GET /s3/{library-path}` without the bucket,
  when its first segment is not a bucket name

The gateway rejects multipart; PutObject writes through the MediaFile
layer onto the first enabled provider. DeleteObject / DeleteObjects
soft-delete the library entry (provider bytes stay).
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field

from server.config import Settings

from .auth import (
    S3AuthIdentity,
    generate_presigned_url,
    public_base_url,
    verify_request_signature_for_identity,
)
from .chunked import unwrap_aws_chunked_body
from .exceptions import InvalidArgument, S3Error
from .factory import build_s3_service
from .services import S3ObjectService
from .vhost import virtual_host_bucket
from .xml import list_multipart_uploads_xml, ownership_controls_xml

router = APIRouter(tags=["S3"])


@dataclass(frozen=True)
class S3RequestContext:
    """Authenticated S3 request: the raw body and the resolved key owner."""

    body: bytes
    identity: S3AuthIdentity


async def s3_auth(request: Request) -> S3RequestContext:
    """Verify SigV4 credentials and return the already-read body."""
    body = await request.body()
    identity = await verify_request_signature_for_identity(request, body)
    return S3RequestContext(body=body, identity=identity)


S3Context = Annotated[S3RequestContext, Depends(s3_auth)]


async def get_service(
    request: Request, context: S3Context,
) -> S3ObjectService:
    """An `S3ObjectService` scoped to the credential's owner."""
    is_admin = False
    auth = getattr(request.app.state, "auth_service", None)
    if auth is not None:
        summary = await auth.get_user_summary(context.identity.user_id)
        if summary is not None:
            is_admin = "admin" in (summary.roles or [])
    return build_s3_service(
        request.app.state,
        user_id=context.identity.user_id,
        is_admin=is_admin,
    )


S3Service = Annotated[S3ObjectService, Depends(get_service)]


def bucket_subresource_response(request: Request, bucket: str) -> Response | None:
    """Cyberduck probes bucket subresources before PutObject.

    `uploads` and `ownershipControls` must not fall through to ListObjects
    (that XML is huge and the wrong schema). Multipart is unsupported, so
    the uploads listing is empty.
    """
    params = request.query_params
    if "uploads" in params:
        return Response(
            content=list_multipart_uploads_xml(bucket),
            media_type="application/xml",
        )
    if "ownershipControls" in params:
        return Response(
            content=ownership_controls_xml(),
            media_type="application/xml",
        )
    if "location" in params:
        return Response(
            content=(
                b'<?xml version="1.0" encoding="UTF-8"?>\n'
                b"<LocationConstraint "
                b'xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                b"</LocationConstraint>"
            ),
            media_type="application/xml",
        )
    return None


class PresignRequest(BaseModel):
    """`POST /s3/presign` body. `key` is a MediaFile library path."""

    key: str
    method: str = "GET"
    expires_in: int | None = None
    bucket: str = Field(default_factory=lambda: Settings.S3_COMPAT_BUCKET)


class PresignResponse(BaseModel):
    """A minted presigned URL."""

    url: str
    expires_in: int


@router.post("/presign", response_model=PresignResponse)
async def presign_url(
    request: Request,
    payload: PresignRequest,
    context: S3Context,
) -> PresignResponse:
    """Mint a presigned URL signed with the *caller's own* credentials."""
    if payload.bucket != Settings.S3_COMPAT_BUCKET:
        raise InvalidArgument("Unknown bucket")
    expires_in = payload.expires_in or Settings.S3_COMPAT_PRESIGNED_EXPIRY
    url = generate_presigned_url(
        method=payload.method,
        key=payload.key,
        expires_in=expires_in,
        base_url=public_base_url(request),
        access_key=context.identity.access_key,
        secret_key=context.identity.secret_key,
    )
    return PresignResponse(url=url, expires_in=expires_in)


_S3_PROBE_HEADERS = {
    "Server": "AmazonS3",
    "x-amz-request-id": "umedia",
    "x-amz-id-2": "umedia",
}


@router.head("")
@router.head("/")
async def head_s3_endpoint() -> Response:
    """Cyberduck (and rclone) probe the service root with HEAD before listing."""
    return Response(status_code=200, headers=_S3_PROBE_HEADERS)


@router.get("")
@router.get("/")
async def list_buckets(
    request: Request,
    service: S3Service,
    list_type: Annotated[str | None, Query(alias="list-type")] = None,
    prefix: str = "",
    delimiter: str = "",
    max_keys: Annotated[int, Query(alias="max-keys")] = 1000,
    continuation_token: Annotated[
        str | None, Query(alias="continuation-token"),
    ] = None,
    marker: str | None = None,
) -> Response:
    """ListBuckets, or ListObjects when Host is `{bucket}.{endpoint}`.

    Cyberduck after walking Path to `/` lists with `encoding-type` /
    `delimiter` on the service root — treat that as ListObjects on the
    configured bucket rather than HTML from the website.
    """
    vhost_bucket = virtual_host_bucket(request)
    bucket = vhost_bucket or Settings.S3_COMPAT_BUCKET
    subresource = bucket_subresource_response(request, bucket)
    if subresource is not None:
        return subresource
    list_objects_on_root = (
        request.url.path in {"/", ""}
        and request.query_params.get("x-id") != "ListBuckets"
        and bool(delimiter or request.query_params.get("encoding-type"))
    )
    if vhost_bucket or list_objects_on_root:
        if list_type not in (None, "2"):
            raise InvalidArgument("Unsupported bucket operation")
        content = await service.list_objects_v2(
            bucket=bucket,
            prefix=prefix,
            delimiter=delimiter,
            max_keys=max_keys,
            continuation_token=continuation_token or marker,
        )
        return Response(content=content, media_type="application/xml")
    content = await service.list_buckets()
    return Response(content=content, media_type="application/xml")


@router.get("/{bucket}")
async def list_objects_v2(
    bucket: str,
    request: Request,
    service: S3Service,
    list_type: Annotated[str | None, Query(alias="list-type")] = None,
    prefix: str = "",
    delimiter: str = "",
    max_keys: Annotated[int, Query(alias="max-keys")] = 1000,
    continuation_token: Annotated[
        str | None, Query(alias="continuation-token"),
    ] = None,
    marker: str | None = None,
) -> Response:
    """ListObjectsV2 (the v1 `marker` doubles as the start-after token)."""
    subresource = bucket_subresource_response(request, bucket)
    if subresource is not None:
        return subresource
    if list_type not in (None, "2"):
        raise InvalidArgument("Unsupported bucket operation")
    content = await service.list_objects_v2(
        bucket=bucket,
        prefix=prefix,
        delimiter=delimiter,
        max_keys=max_keys,
        continuation_token=continuation_token or marker,
    )
    return Response(content=content, media_type="application/xml")


@router.post("")
@router.post("/")
async def post_s3_root(
    request: Request,
    context: S3Context,
    service: S3Service,
) -> Response:
    """DeleteObjects on virtual-host `/` or path-style `/s3?delete`."""
    if "delete" not in request.query_params:
        raise InvalidArgument("Unsupported bucket operation")
    bucket = virtual_host_bucket(request) or Settings.S3_COMPAT_BUCKET
    return await service.delete_objects(bucket=bucket, body=context.body)


@router.post("/{bucket}")
async def post_bucket(
    bucket: str,
    request: Request,
    context: S3Context,
    service: S3Service,
) -> Response:
    if "delete" not in request.query_params:
        raise InvalidArgument("Unsupported bucket operation")
    return await service.delete_objects(bucket=bucket, body=context.body)


@router.head("/{bucket}")
async def head_bucket(bucket: str, service: S3Service) -> Response:
    return await service.head_bucket(bucket)


@router.put("/{bucket}")
async def create_bucket(bucket: str, service: S3Service) -> Response:
    return await service.create_bucket(bucket)


@router.get("/{first}/{rest:path}", response_model=None)
async def get_object(
    request: Request,
    first: str,
    rest: str,
    service: S3Service,
) -> Response:
    bucket, key = await service.split_path(first, rest)
    return await service.get_object(
        bucket=bucket,
        key=key,
        range_header=request.headers.get("Range"),
    )


@router.head("/{first}/{rest:path}", response_model=None)
async def head_object(
    first: str,
    rest: str,
    service: S3Service,
) -> Response:
    bucket, key = await service.split_path(first, rest)
    return await service.head_object(bucket=bucket, key=key)


@router.put("/{first}/{rest:path}", response_model=None)
async def put_object(
    request: Request,
    first: str,
    rest: str,
    context: S3Context,
    service: S3Service,
) -> Response:
    bucket, key = await service.split_path(first, rest)
    return await service.put_object(
        bucket=bucket,
        key=key,
        body=unwrap_aws_chunked_body(context.body),
        content_type=request.headers.get("content-type"),
    )


@router.post("/{first}/{rest:path}", response_model=None)
async def post_object(first: str, rest: str, _: S3Context) -> Response:
    raise InvalidArgument("Multipart uploads are not supported")


@router.delete("/{first}/{rest:path}", response_model=None)
async def delete_object(
    first: str,
    rest: str,
    service: S3Service,
) -> Response:
    bucket, key = await service.split_path(first, rest)
    return await service.delete_object(bucket=bucket, key=key)


def register_s3_exception_handler(app: object) -> None:
    """Map every `S3Error` to its S3-style XML error response."""
    from fastapi import FastAPI

    if not isinstance(app, FastAPI):
        return

    def _handler(_: Request, exc: S3Error) -> Response:
        return exc.to_response()

    app.add_exception_handler(S3Error, _handler)


# Service root plus path-style `/{bucket}/{key}` using the configured
# bucket name as a *literal* path (never `/{bucket}` as a param, which
# would steal `/api/v1/...`). MinIO `mcli` talks to the host root, not
# `/api/v1/s3`.
_COMPAT_BUCKET = Settings.S3_COMPAT_BUCKET


async def root_list_objects(
    request: Request,
    service: S3Service,
    list_type: Annotated[str | None, Query(alias="list-type")] = None,
    prefix: str = "",
    delimiter: str = "",
    max_keys: Annotated[int, Query(alias="max-keys")] = 1000,
    continuation_token: Annotated[
        str | None, Query(alias="continuation-token"),
    ] = None,
    marker: str | None = None,
) -> Response:
    return await list_objects_v2(
        bucket=_COMPAT_BUCKET,
        request=request,
        service=service,
        list_type=list_type,
        prefix=prefix,
        delimiter=delimiter,
        max_keys=max_keys,
        continuation_token=continuation_token,
        marker=marker,
    )


async def root_head_bucket(service: S3Service) -> Response:
    return await head_bucket(_COMPAT_BUCKET, service)


async def root_get_object(
    request: Request, rest: str, service: S3Service,
) -> Response:
    return await get_object(request, _COMPAT_BUCKET, rest, service)


async def root_head_object(rest: str, service: S3Service) -> Response:
    return await head_object(_COMPAT_BUCKET, rest, service)


async def root_put_object(
    request: Request, rest: str, context: S3Context, service: S3Service,
) -> Response:
    return await put_object(request, _COMPAT_BUCKET, rest, context, service)


async def root_post_object(rest: str, context: S3Context) -> Response:
    return await post_object(_COMPAT_BUCKET, rest, context)


async def root_delete_object(rest: str, service: S3Service) -> Response:
    return await delete_object(_COMPAT_BUCKET, rest, service)


async def root_post_bucket(
    request: Request, context: S3Context, service: S3Service,
) -> Response:
    return await post_bucket(_COMPAT_BUCKET, request, context, service)


async def root_post_delete(
    request: Request, context: S3Context, service: S3Service,
) -> Response:
    return await post_s3_root(request, context, service)


root_router = APIRouter(tags=["S3"])
root_router.add_api_route("/", head_s3_endpoint, methods=["HEAD"])
root_router.add_api_route("/", list_buckets, methods=["GET"])
root_router.add_api_route("/", root_post_delete, methods=["POST"])
root_router.add_api_route(f"/{_COMPAT_BUCKET}", root_head_bucket, methods=["HEAD"])
root_router.add_api_route(f"/{_COMPAT_BUCKET}", root_list_objects, methods=["GET"])
root_router.add_api_route(f"/{_COMPAT_BUCKET}", root_post_bucket, methods=["POST"])
root_router.add_api_route(f"/{_COMPAT_BUCKET}/", root_head_bucket, methods=["HEAD"])
root_router.add_api_route(f"/{_COMPAT_BUCKET}/", root_list_objects, methods=["GET"])
root_router.add_api_route(f"/{_COMPAT_BUCKET}/", root_post_bucket, methods=["POST"])
root_router.add_api_route(
    f"/{_COMPAT_BUCKET}/{{rest:path}}",
    root_get_object,
    methods=["GET"],
    response_model=None,
)
root_router.add_api_route(
    f"/{_COMPAT_BUCKET}/{{rest:path}}",
    root_head_object,
    methods=["HEAD"],
    response_model=None,
)
root_router.add_api_route(
    f"/{_COMPAT_BUCKET}/{{rest:path}}",
    root_put_object,
    methods=["PUT"],
    response_model=None,
)
root_router.add_api_route(
    f"/{_COMPAT_BUCKET}/{{rest:path}}",
    root_post_object,
    methods=["POST"],
    response_model=None,
)
root_router.add_api_route(
    f"/{_COMPAT_BUCKET}/{{rest:path}}",
    root_delete_object,
    methods=["DELETE"],
    response_model=None,
)
