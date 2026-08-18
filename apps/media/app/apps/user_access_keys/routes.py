from fastapi import APIRouter, Request, status
from fastapi_mongo_base.errors import NotFoundError

from apps.s3.auth import public_base_url
from server.config import Settings

from .api_schemas import (
    AccessKeyCreateIn,
    AccessKeyCreatedOut,
    AccessKeyOut,
    S3EndpointOut,
)
from .factory import build_user_access_key_service_from_state
from .services import UserAccessKeyService

router = APIRouter(prefix="/access-keys", tags=["Access keys"])


def _service(request: Request) -> UserAccessKeyService:
    return build_user_access_key_service_from_state(request.app.state)


@router.get("/s3", response_model=S3EndpointOut)
async def get_s3_connection_info(request: Request) -> S3EndpointOut:
    return S3EndpointOut(
        endpoint=f"{public_base_url(request)}{Settings.base_path}/s3",
        region=Settings.S3_COMPAT_REGION,
        bucket=Settings.S3_COMPAT_BUCKET,
    )


@router.get("", response_model=list[AccessKeyOut])
async def list_access_keys(request: Request) -> list[AccessKeyOut]:
    records = await _service(request).list_for_user(request.state.user.uid)
    return [AccessKeyOut.from_record(record) for record in records]


@router.post(
    "",
    response_model=AccessKeyCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_access_key(
    request: Request,
    data: AccessKeyCreateIn | None = None,
) -> AccessKeyCreatedOut:
    created = await _service(request).create_key(
        request.state.user.uid,
        label=data.label if data is not None else "key",
    )
    public = AccessKeyOut.from_record(created.record)
    return AccessKeyCreatedOut(
        **public.model_dump(),
        secret_access_key=created.secret_access_key,
    )


@router.delete("/{uid}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_access_key(uid: str, request: Request) -> None:
    deactivated = await _service(request).deactivate_for_user(
        uid,
        request.state.user.uid,
    )
    if deactivated is None:
        raise NotFoundError(
            error_code="access_key_not_found",
            detail="Access key not found",
            message="Access key not found",
        )
