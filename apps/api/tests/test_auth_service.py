import pytest

from apps.auth.security import PasswordHasher
from apps.auth.services import (
    AlreadyConfiguredError,
    AuthenticationError,
    AuthService,
)


class FakeAuthRepository:
    def __init__(self) -> None:
        self.email: str | None = None
        self.password_hash: str | None = None
        self.password_version = 0

    async def get(self) -> tuple[str, str, int] | None:
        if self.password_hash is None:
            return None
        return self.email or "", self.password_hash, self.password_version

    async def create(
        self,
        email: str,
        password_hash: str,
    ) -> tuple[str, str, int]:
        self.email = email
        self.password_hash = password_hash
        self.password_version = 1
        return email, password_hash, 1

    async def change_password(
        self,
        password_hash: str,
    ) -> tuple[str, str, int]:
        self.password_hash = password_hash
        self.password_version += 1
        return self.email or "", password_hash, self.password_version


@pytest.mark.asyncio
async def test_setup_is_single_use() -> None:
    service = AuthService(FakeAuthRepository(), PasswordHasher())

    configured = await service.setup(
        "Admin@Example.com",
        "a secure first password",
    )

    assert configured[0] == "admin@example.com"
    assert configured[2] == 1
    with pytest.raises(AlreadyConfiguredError):
        await service.setup("other@example.com", "another secure password")


@pytest.mark.asyncio
async def test_login_and_password_change_require_current_password() -> None:
    service = AuthService(FakeAuthRepository(), PasswordHasher())
    await service.setup("admin@example.com", "a secure first password")

    assert (
        await service.authenticate(
            "ADMIN@example.com",
            "a secure first password",
        )
        == 1
    )
    with pytest.raises(AuthenticationError):
        await service.authenticate("admin@example.com", "wrong password")

    new_version = await service.change_password(
        current_password="a secure first password",
        new_password="a different secure password",
    )

    assert new_version == 2
    assert (
        await service.authenticate(
            "admin@example.com",
            "a different secure password",
        )
        == 2
    )
