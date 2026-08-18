"""Authentication request and response schemas."""

from typing import Literal

from pydantic import BaseModel, EmailStr, Field

#: UMedia's own password floor (stricter than usso.lite's 8) -- enforced
#: both here (request validation) and in `AuthService` (business rule).
MIN_PASSWORD_LENGTH = 12

#: The two roles UMedia knows about. "admin" manages users and provider
#: connections; "user" gets read access to the shared library surface.
Role = Literal["admin", "user"]


class CredentialsRequest(BaseModel):
    """Email and password request (setup and login)."""

    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=1024)


class PasswordChangeRequest(BaseModel):
    """Password change request."""

    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=1024)


class AuthStateUser(BaseModel):
    """The authenticated caller, as exposed by `GET /auth/state`."""

    uid: str
    email: str
    roles: list[str]


class AuthStateResponse(BaseModel):
    """Public installation authentication state."""

    configured: bool
    authenticated: bool
    user: AuthStateUser | None = None


class SessionResponse(AuthStateResponse):
    """Authenticated session response for browser and API clients."""

    access_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int


class CurrentSessionResponse(BaseModel):
    """Who the current session belongs to (`GET /auth/sessions/current`)."""

    uid: str
    email: str
    roles: list[str]
    name: str | None = None


class UserSummary(BaseModel):
    """A user as UMedia presents it: one primary email, one role list."""

    uid: str
    email: str
    name: str | None = None
    roles: list[str]
    is_active: bool


class UserCreateRequest(BaseModel):
    """Create a user (admin endpoint)."""

    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=1024)
    role: Role = "user"
    name: str | None = Field(default=None, max_length=255)


class UserUpdateRequest(BaseModel):
    """Update a user (admin endpoint).

    `name` distinguishes "not sent" from an explicit `null` (which clears
    the name) via `model_fields_set` in the route.
    """

    name: str | None = Field(default=None, max_length=255)
    role: Role | None = None
    is_active: bool | None = None
