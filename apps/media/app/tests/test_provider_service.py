import pytest

from apps.provider_connections.services import (
    ProviderConnectionService,
    ProviderValidationError,
)
from plugins.manifest import ConfigField, PluginManifest


class FakeCipher:
    def encrypt_json(self, value: dict) -> str:
        return f"encrypted:{sorted(value)}"


class FakeRepository:
    def __init__(self) -> None:
        self.created: dict | None = None
        self._rows: dict[str, dict] = {}

    async def create(self, data: dict) -> dict:
        self.created = data
        record = data | {"uid": "connection-id", "enabled": True}
        self._rows[record["uid"]] = record
        return record

    async def get(self, uid: str) -> dict | None:
        return self._rows.get(uid)

    async def update(self, uid: str, changes: dict) -> dict | None:
        if uid not in self._rows:
            return None
        self._rows[uid] = self._rows[uid] | changes
        return self._rows[uid]


LOCAL_MANIFEST = PluginManifest(
    id="local",
    name="Local filesystem",
    description="A directory mounted into the container.",
    entrypoint=["python", "-m", "plugins.local.main"],
    config_fields=(ConfigField(key="root_path", label="Root path"),),
    capabilities=("list", "read", "write", "delete", "move", "copy"),
)

TELEGRAM_MANIFEST = PluginManifest(
    id="telegram",
    name="Telegram",
    description="A Telegram channel.",
    entrypoint=["python", "-m", "plugins.telegram.main"],
    config_fields=(ConfigField(key="api_id", label="API ID"),),
    capabilities=("list", "read", "write", "delete", "copy"),
)


class FakeRegistry:
    def __init__(self, manifests: dict[str, PluginManifest]) -> None:
        self._manifests = manifests

    def get(self, provider_type: str) -> PluginManifest | None:
        return self._manifests.get(provider_type)


async def passing_connect(  # noqa: RUF029 -- matches the real Connector's async signature
    _manifest: PluginManifest, _config: dict,
) -> None:
    return None


@pytest.mark.asyncio
async def test_create_connection_encrypts_secrets() -> None:
    repository = FakeRepository()
    service = ProviderConnectionService(
        repository,
        FakeCipher(),
        FakeRegistry({"local": LOCAL_MANIFEST}),
        passing_connect,
    )

    result = await service.create(
        provider_type="local",
        name="Library",
        config={"root_path": "/storage/library"},
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
        FakeRegistry({"local": LOCAL_MANIFEST}),
        passing_connect,
    )

    with pytest.raises(ProviderValidationError):
        await service.create(provider_type="local", name="Local", config={})


@pytest.mark.asyncio
async def test_unknown_provider_type_is_rejected() -> None:
    service = ProviderConnectionService(
        FakeRepository(), FakeCipher(), FakeRegistry({}), passing_connect,
    )

    with pytest.raises(ProviderValidationError):
        await service.create(provider_type="not-real", name="X", config={})


@pytest.mark.asyncio
async def test_update_renames_and_toggles_enabled() -> None:
    repository = FakeRepository()
    service = ProviderConnectionService(
        repository,
        FakeCipher(),
        FakeRegistry({"local": LOCAL_MANIFEST}),
        passing_connect,
    )
    created = await service.create(
        provider_type="local", name="Library", config={"root_path": "/x"},
    )

    updated = await service.update(created["uid"], name="Renamed", enabled=False)

    assert updated["name"] == "Renamed"
    assert updated["enabled"] is False


@pytest.mark.asyncio
async def test_update_rejects_an_empty_name() -> None:
    repository = FakeRepository()
    service = ProviderConnectionService(
        repository,
        FakeCipher(),
        FakeRegistry({"local": LOCAL_MANIFEST}),
        passing_connect,
    )
    created = await service.create(
        provider_type="local", name="Library", config={"root_path": "/x"},
    )

    with pytest.raises(ProviderValidationError):
        await service.update(created["uid"], name="   ")


@pytest.mark.asyncio
async def test_update_with_no_fields_returns_the_current_record() -> None:
    repository = FakeRepository()
    service = ProviderConnectionService(
        repository,
        FakeCipher(),
        FakeRegistry({"local": LOCAL_MANIFEST}),
        passing_connect,
    )
    created = await service.create(
        provider_type="local", name="Library", config={"root_path": "/x"},
    )

    result = await service.update(created["uid"])

    assert result["name"] == "Library"


@pytest.mark.asyncio
async def test_update_missing_connection_returns_none() -> None:
    service = ProviderConnectionService(
        FakeRepository(), FakeCipher(), FakeRegistry({}), passing_connect,
    )

    assert await service.update("does-not-exist", name="X") is None


@pytest.mark.asyncio
async def test_create_rejects_mirror_structure_without_move_capability() -> None:
    service = ProviderConnectionService(
        FakeRepository(),
        FakeCipher(),
        FakeRegistry({"telegram": TELEGRAM_MANIFEST}),
        passing_connect,
    )

    with pytest.raises(ProviderValidationError, match="mirror_structure"):
        await service.create(
            provider_type="telegram",
            name="Channel",
            config={"api_id": "1"},
            mirror_structure=True,
        )


@pytest.mark.asyncio
async def test_update_rejects_enabling_mirror_structure_without_move() -> None:
    repository = FakeRepository()
    service = ProviderConnectionService(
        repository,
        FakeCipher(),
        FakeRegistry({"telegram": TELEGRAM_MANIFEST, "local": LOCAL_MANIFEST}),
        passing_connect,
    )
    created = await service.create(
        provider_type="telegram",
        name="Channel",
        config={"api_id": "1"},
        mirror_structure=False,
    )

    with pytest.raises(ProviderValidationError, match="mirror_structure"):
        await service.update(created["uid"], mirror_structure=True)


@pytest.mark.asyncio
async def test_update_can_toggle_flags_when_provider_supports_mirror() -> None:
    repository = FakeRepository()
    service = ProviderConnectionService(
        repository,
        FakeCipher(),
        FakeRegistry({"local": LOCAL_MANIFEST}),
        passing_connect,
    )
    created = await service.create(
        provider_type="local",
        name="Library",
        config={"root_path": "/x"},
    )

    updated = await service.update(
        created["uid"],
        import_existing=True,
        mirror_structure=True,
    )

    assert updated["import_existing"] is True
    assert updated["mirror_structure"] is True
