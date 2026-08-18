import pytest

from apps.provider_connections.services import (
    ProviderConnectionService,
    ProviderValidationError,
)


class FakeCipher:
    def encrypt_json(self, value: dict) -> str:
        return f"encrypted:{sorted(value)}"


class FakeRepository:
    def __init__(self) -> None:
        self.created: dict | None = None

    async def create(self, data: dict) -> dict:
        self.created = data
        return data | {"uid": "connection-id"}


class PassingProvider:
    async def test_connection(self) -> None:
        return None


def provider_factory(_: str, __: dict) -> PassingProvider:
    return PassingProvider()


@pytest.mark.asyncio
async def test_create_connection_encrypts_secrets() -> None:
    repository = FakeRepository()
    service = ProviderConnectionService(
        repository,
        FakeCipher(),
        provider_factory,
    )

    result = await service.create(
        provider_type="s3",
        name="Backblaze",
        config={
            "endpoint_url": "https://s3.example.com",
            "access_key_id": "access",
            "secret_access_key": "secret",
            "bucket": "media",
        },
    )

    assert result["uid"] == "connection-id"
    assert repository.created is not None
    assert "config" not in repository.created
    assert repository.created["encrypted_config"].startswith("encrypted:")


@pytest.mark.asyncio
async def test_missing_required_provider_field_is_rejected() -> None:
    service = ProviderConnectionService(
        FakeRepository(),
        FakeCipher(),
        provider_factory,
    )

    with pytest.raises(ProviderValidationError):
        await service.create(
            provider_type="local",
            name="Local",
            config={},
        )
