"""Provider connection REST resources."""

from fastapi import APIRouter, Request, Response, status
from fastapi_mongo_base.errors import NotFoundError

from providers.catalog import PROVIDER_CATALOG

from .repository import ProviderConnectionRepository
from .schemas import (
    ProviderConnectionCreate,
    ProviderConnectionResponse,
    ProviderTypeResponse,
)
from .services import ProviderConnectionService

router = APIRouter(tags=["Storage providers"])


def _repository(request: Request) -> ProviderConnectionRepository:
    return ProviderConnectionRepository(request.app.state.session_factory)


def _response(item: object) -> ProviderConnectionResponse:
    return ProviderConnectionResponse(
        uid=item.uid,
        provider_type=item.provider_type,
        name=item.name,
        status=item.status,
        created_at=item.created_at,
        last_tested_at=item.last_tested_at,
        last_error=item.last_error,
    )


@router.get("/provider-types", response_model=list[ProviderTypeResponse])
async def list_provider_types() -> list[ProviderTypeResponse]:
    """List supported connection types and their configuration contract."""
    return [
        ProviderTypeResponse(
            id=item.id,
            name=item.name,
            description=item.description,
            adapter=item.adapter,
            status=item.status,
            capabilities=list(item.capabilities),
            fields=[
                {
                    "key": field.key,
                    "label": field.label,
                    "input_type": field.input_type,
                    "required": field.required,
                    "secret": field.secret,
                    "placeholder": field.placeholder,
                }
                for field in item.fields
            ],
        )
        for item in PROVIDER_CATALOG.values()
    ]


@router.get(
    "/provider-connections",
    response_model=list[ProviderConnectionResponse],
)
async def list_connections(request: Request) -> list[ProviderConnectionResponse]:
    """List configured storage connections without credentials."""
    return [_response(item) for item in await _repository(request).list()]


@router.post(
    "/provider-connections",
    response_model=ProviderConnectionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_connection(
    data: ProviderConnectionCreate,
    request: Request,
) -> ProviderConnectionResponse:
    """Validate, encrypt and persist a storage connection."""
    service = ProviderConnectionService(
        _repository(request),
        request.app.state.credential_cipher,
    )
    return _response(
        await service.create(
            provider_type=data.provider_type,
            name=data.name,
            config=data.config,
        ),
    )


@router.delete(
    "/provider-connections/{uid}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_connection(uid: str, request: Request) -> Response:
    """Soft-delete a provider connection."""
    if not await _repository(request).delete(uid):
        raise NotFoundError(
            error_code="provider_connection_not_found",
            detail="Provider connection not found",
            message="Provider connection not found",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)

