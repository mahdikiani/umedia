"""Authentication business rules."""

import hmac
from typing import Protocol

from fastapi_mongo_base.core.exceptions import BaseHTTPException

from .security import PasswordHasher


class AuthRepositoryProtocol(Protocol):
    """Storage operations required by authentication."""

    async def get(self) -> tuple[str, str, int] | None: ...

    async def create(
        self,
        email: str,
        password_hash: str,
    ) -> tuple[str, str, int]: ...

    async def change_password(
        self,
        password_hash: str,
    ) -> tuple[str, str, int]: ...


class AlreadyConfiguredError(BaseHTTPException):
    """Raised when initial setup has already completed."""

    def __init__(self) -> None:
        super().__init__(
            status_code=409,
            error_code="already_configured",
            detail="Administrator password is already configured",
            message="Administrator password is already configured",
        )


class AuthenticationError(BaseHTTPException):
    """Raised for invalid credentials."""

    def __init__(self) -> None:
        super().__init__(
            status_code=401,
            error_code="invalid_credentials",
            detail="Invalid password",
            message="Invalid password",
        )


class NotConfiguredError(BaseHTTPException):
    """Raised when login is attempted before setup."""

    def __init__(self) -> None:
        super().__init__(
            status_code=409,
            error_code="not_configured",
            detail="Administrator password has not been configured",
            message="Administrator password has not been configured",
        )


class AuthService:
    """Configure and authenticate the installation administrator."""

    def __init__(
        self,
        repository: AuthRepositoryProtocol,
        hasher: PasswordHasher,
    ) -> None:
        self._repository = repository
        self._hasher = hasher

    async def is_configured(self) -> bool:
        """Return whether first-run setup is complete."""
        return await self._repository.get() is not None

    async def get_state(self) -> tuple[str, str, int] | None:
        """Return the current persisted authentication state."""
        return await self._repository.get()

    async def setup(
        self,
        email: str,
        password: str,
    ) -> tuple[str, str, int]:
        """Set the installation password once."""
        if await self._repository.get() is not None:
            raise AlreadyConfiguredError
        normalized_email = email.strip().casefold()
        return await self._repository.create(
            normalized_email,
            self._hasher.hash(password),
        )

    async def authenticate(self, email: str, password: str) -> int:
        """Validate a password and return its session version."""
        state = await self._repository.get()
        if state is None:
            raise NotConfiguredError
        stored_email, password_hash, password_version = state
        normalized_email = email.strip().casefold()
        valid_password = self._hasher.verify(password, password_hash)
        valid_email = hmac.compare_digest(normalized_email, stored_email)
        if not valid_email or not valid_password:
            raise AuthenticationError
        return password_version

    async def change_password(
        self,
        *,
        current_password: str,
        new_password: str,
    ) -> int:
        """Verify the current password and replace it."""
        state = await self._repository.get()
        if state is None:
            raise NotConfiguredError
        email, _, _ = state
        await self.authenticate(email, current_password)
        _, _, version = await self._repository.change_password(
            self._hasher.hash(new_password),
        )
        return version
