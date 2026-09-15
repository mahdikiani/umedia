"""Instance placement settings -- where root uploads and new root folders land."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from apps.auth.middleware import require_admin
from apps.provider_connections.repository import ProviderConnectionRepository

from .api_schemas import PlacementIn, PlacementOut
from .errors import MediaFileValidationError
from .placement import POLICIES
from .settings_repository import InstanceSettingsRepository

router = APIRouter(prefix="/settings", tags=["Settings"])


def _repo(request: Request) -> InstanceSettingsRepository:
    return InstanceSettingsRepository(request.app.state.session_factory)


@router.get("/placement", response_model=PlacementOut)
async def get_placement(request: Request) -> PlacementOut:
    current = await _repo(request).get()
    return PlacementOut(
        policy=current.policy,
        default_connection_id=current.default_connection_id,
        fill_order=list(current.fill_order),
    )


@router.patch("/placement", response_model=PlacementOut)
async def update_placement(
    body: PlacementIn,
    request: Request,
    admin: Annotated[object, Depends(require_admin)],
) -> PlacementOut:
    del admin
    changes: dict[str, object] = {}
    if body.policy is not None:
        if body.policy not in POLICIES:
            raise MediaFileValidationError(f"Unknown placement policy '{body.policy}'")
        changes["placement_policy"] = body.policy
    if "default_connection_id" in body.model_fields_set:
        changes["default_connection_id"] = body.default_connection_id or None
    if body.fill_order is not None:
        changes["fill_order"] = body.fill_order
    if changes.get("default_connection_id") or changes.get("fill_order"):
        known = {
            connection.uid
            for connection in await ProviderConnectionRepository(
                request.app.state.session_factory,
            ).list()
        }
        default_id = changes.get("default_connection_id")
        if default_id and default_id not in known:
            raise MediaFileValidationError(
                f"Unknown provider_connection_id '{default_id}'",
            )
        for uid in changes.get("fill_order") or []:
            if uid not in known:
                raise MediaFileValidationError(
                    f"Unknown provider_connection_id '{uid}'",
                )
    current = (
        await _repo(request).update(changes)
        if changes
        else await _repo(request).get()
    )
    return PlacementOut(
        policy=current.policy,
        default_connection_id=current.default_connection_id,
        fill_order=list(current.fill_order),
    )
