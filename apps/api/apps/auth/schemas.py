"""Authentication request and response schemas."""

from pydantic import BaseModel, EmailStr, Field


class CredentialsRequest(BaseModel):
    """Administrator email and password request."""

    email: EmailStr
    password: str = Field(min_length=12, max_length=1024)


class PasswordChangeRequest(BaseModel):
    """Password change request."""

    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)


class AuthStateResponse(BaseModel):
    """Public installation authentication state."""

    configured: bool
    authenticated: bool


class SessionResponse(AuthStateResponse):
    """Authenticated session response for browser and API clients."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int
