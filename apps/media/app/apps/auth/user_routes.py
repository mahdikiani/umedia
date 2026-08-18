"""REST user management routes -- administrators only.

Thin HTTP layer over `AuthService`'s user management: every rule (last
administrator protection, self-deletion, password minimum, primary email
resolution) lives in the service, not here.
"""

from fastapi import APIRouter, Depends, Request, status

from .middleware import require_admin
from .schemas import UserCreateRequest, UserSummary, UserUpdateRequest
from .services import AuthService

router = APIRouter(
    prefix="/users",
    tags=["Users"],
    dependencies=[Depends(require_admin)],
)


def _service(request: Request) -> AuthService:
    return request.app.state.auth_service


@router.get("", response_model=list[UserSummary])
async def list_users(request: Request) -> list[UserSummary]:
    """List every account with its primary email and role."""
    return await _service(request).list_users()


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=UserSummary,
)
async def create_user(
    data: UserCreateRequest,
    request: Request,
) -> UserSummary:
    """Create an account with one email identifier and one role."""
    return await _service(request).create_user(
        email=str(data.email),
        password=data.password,
        role=data.role,
        name=data.name,
    )


@router.patch("/{uid}", response_model=UserSummary)
async def update_user(
    uid: str,
    data: UserUpdateRequest,
    request: Request,
) -> UserSummary:
    """Update an account's profile, role and/or active state."""
    kwargs: dict[str, object] = {}
    # An explicit `"name": null` clears the name; an absent key leaves it.
    if "name" in data.model_fields_set:
        kwargs["name"] = data.name
    if data.role is not None:
        kwargs["role"] = data.role
    if data.is_active is not None:
        kwargs["is_active"] = data.is_active
    return await _service(request).update_user(uid, **kwargs)


@router.delete("/{uid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(uid: str, request: Request) -> None:
    """Soft-delete an account (never your own, never the last admin)."""
    await _service(request).delete_user(
        uid,
        actor_uid=request.state.user.uid,
    )
