from types import SimpleNamespace

import pytest

from apps.provider_connections.services import (
    LocalProviderAdminRequired,
    ProviderConnectionService,
    ProviderValidationError,
    connection_usable_by,
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

    async def get(
        self, uid: str, *, owner_id: str | None = None,
    ) -> dict | None:
        row = self._rows.get(uid)
        if row is None:
            return None
        if owner_id is not None and row.get("owner_id") != owner_id:
            return None
        return row

    async def update(
        self, uid: str, changes: dict, *, owner_id: str | None = None,
    ) -> dict | None:
        current = await self.get(uid, owner_id=owner_id)
        if current is None:
            return None
        self._rows[uid] = current | changes
        return self._rows[uid]

    async def delete(
        self, uid: str, *, owner_id: str | None = None,
    ) -> bool:
        current = await self.get(uid, owner_id=owner_id)
        if current is None:
            return False
        del self._rows[uid]
        return True


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


def _service(
    repository: FakeRepository | None = None,
    manifests: dict[str, PluginManifest] | None = None,
) -> tuple[ProviderConnectionService, FakeRepository]:
    repo = repository or FakeRepository()
    return ProviderConnectionService(
        repo,
        FakeCipher(),
        FakeRegistry(manifests or {"local": LOCAL_MANIFEST}),
        passing_connect,
    ), repo


@pytest.mark.asyncio
async def test_create_connection_encrypts_secrets() -> None:
    service, repository = _service()

    result = await service.create(
        provider_type="local",
        name="Library",
        config={"root_path": "/storage/library"},
        owner_id="admin-1",
        is_admin=True,
    )

    assert result["uid"] == "connection-id"
    assert repository.created is not None
    assert "config" not in repository.created
    assert repository.created["encrypted_config"].startswith("encrypted:")
    assert repository.created["owner_id"] == "admin-1"


@pytest.mark.asyncio
async def test_create_sets_owner_id_from_caller() -> None:
    service, repository = _service(
        manifests={"telegram": TELEGRAM_MANIFEST},
    )

    await service.create(
        provider_type="telegram",
        name="Channel",
        config={"api_id": "1"},
        owner_id="user-42",
        is_admin=False,
    )

    assert repository.created is not None
    assert repository.created["owner_id"] == "user-42"


@pytest.mark.asyncio
async def test_non_admin_cannot_create_local() -> None:
    service, _ = _service()

    with pytest.raises(LocalProviderAdminRequired) as raised:
        await service.create(
            provider_type="local",
            name="Library",
            config={"root_path": "/x"},
            owner_id="user-1",
            is_admin=False,
        )

    assert raised.value.error_code == "local_admin_required"
    assert raised.value.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_create_local() -> None:
    service, repository = _service()

    result = await service.create(
        provider_type="local",
        name="Library",
        config={"root_path": "/x"},
        owner_id="admin-1",
        is_admin=True,
    )

    assert result["owner_id"] == "admin-1"
    assert repository.created["provider_type"] == "local"


def test_connection_usable_by_blocks_local_for_non_admin_owner() -> None:
    local = SimpleNamespace(owner_id="user-1", provider_type="local")
    assert connection_usable_by(
        local, actor_user_id="user-1", is_admin=False,
    ) is False
    assert connection_usable_by(
        local, actor_user_id="user-1", is_admin=True,
    ) is True


def test_connection_usable_by_blocks_unowned_connections() -> None:
    remote = SimpleNamespace(owner_id="owner-a", provider_type="telegram")
    assert connection_usable_by(
        remote, actor_user_id="owner-b", is_admin=True,
    ) is False
    assert connection_usable_by(
        remote, actor_user_id="owner-a", is_admin=False,
    ) is True


@pytest.mark.asyncio
async def test_missing_required_provider_field_is_rejected() -> None:
    service, _ = _service()

    with pytest.raises(ProviderValidationError):
        await service.create(
            provider_type="local",
            name="Local",
            config={},
            owner_id="admin-1",
            is_admin=True,
        )


@pytest.mark.asyncio
async def test_unknown_provider_type_is_rejected() -> None:
    service, _ = _service(manifests={})

    with pytest.raises(ProviderValidationError):
        await service.create(
            provider_type="not-real",
            name="X",
            config={},
            owner_id="admin-1",
            is_admin=True,
        )


@pytest.mark.asyncio
async def test_update_renames_and_toggles_enabled() -> None:
    service, _ = _service()
    created = await service.create(
        provider_type="local",
        name="Library",
        config={"root_path": "/x"},
        owner_id="admin-1",
        is_admin=True,
    )

    updated = await service.update(
        created["uid"],
        owner_id="admin-1",
        name="Renamed",
        enabled=False,
    )

    assert updated["name"] == "Renamed"
    assert updated["enabled"] is False


@pytest.mark.asyncio
async def test_update_for_other_owner_returns_none() -> None:
    service, _ = _service()
    created = await service.create(
        provider_type="local",
        name="Library",
        config={"root_path": "/x"},
        owner_id="admin-1",
        is_admin=True,
    )

    assert await service.update(
        created["uid"], owner_id="other-user", name="Hijack",
    ) is None


@pytest.mark.asyncio
async def test_update_rejects_an_empty_name() -> None:
    service, _ = _service()
    created = await service.create(
        provider_type="local",
        name="Library",
        config={"root_path": "/x"},
        owner_id="admin-1",
        is_admin=True,
    )

    with pytest.raises(ProviderValidationError):
        await service.update(created["uid"], owner_id="admin-1", name="   ")


@pytest.mark.asyncio
async def test_update_with_no_fields_returns_the_current_record() -> None:
    service, _ = _service()
    created = await service.create(
        provider_type="local",
        name="Library",
        config={"root_path": "/x"},
        owner_id="admin-1",
        is_admin=True,
    )

    result = await service.update(created["uid"], owner_id="admin-1")

    assert result["name"] == "Library"


@pytest.mark.asyncio
async def test_update_missing_connection_returns_none() -> None:
    service, _ = _service(manifests={})

    assert await service.update("does-not-exist", name="X") is None


@pytest.mark.asyncio
async def test_create_rejects_mirror_structure_without_move_capability() -> None:
    service, _ = _service(manifests={"telegram": TELEGRAM_MANIFEST})

    with pytest.raises(ProviderValidationError, match="mirror_structure"):
        await service.create(
            provider_type="telegram",
            name="Channel",
            config={"api_id": "1"},
            owner_id="user-1",
            mirror_structure=True,
        )


@pytest.mark.asyncio
async def test_update_rejects_enabling_mirror_structure_without_move() -> None:
    service, _ = _service(
        manifests={"telegram": TELEGRAM_MANIFEST, "local": LOCAL_MANIFEST},
    )
    created = await service.create(
        provider_type="telegram",
        name="Channel",
        config={"api_id": "1"},
        owner_id="user-1",
        mirror_structure=False,
    )

    with pytest.raises(ProviderValidationError, match="mirror_structure"):
        await service.update(
            created["uid"], owner_id="user-1", mirror_structure=True,
        )


@pytest.mark.asyncio
async def test_update_can_toggle_flags_when_provider_supports_mirror() -> None:
    service, _ = _service()
    created = await service.create(
        provider_type="local",
        name="Library",
        config={"root_path": "/x"},
        owner_id="admin-1",
        is_admin=True,
    )

    updated = await service.update(
        created["uid"],
        owner_id="admin-1",
        import_existing=True,
        mirror_structure=True,
    )

    assert updated["import_existing"] is True
    assert updated["mirror_structure"] is True
