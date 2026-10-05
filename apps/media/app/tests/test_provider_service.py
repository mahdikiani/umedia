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
        uid = "connection-id" if not self._rows else f"connection-{len(self._rows)}"
        record = data | {"uid": uid, "enabled": True}
        self._rows[record["uid"]] = record
        return record

    async def list(self, *, owner_id: str | None = None) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(**row)
            for row in self._rows.values()
            if owner_id is None or row.get("owner_id") == owner_id
        ]

    async def get(
        self,
        uid: str,
        *,
        owner_id: str | None = None,
    ) -> dict | None:
        row = self._rows.get(uid)
        if row is None:
            return None
        if owner_id is not None and row.get("owner_id") != owner_id:
            return None
        return row

    async def update(
        self,
        uid: str,
        changes: dict,
        *,
        owner_id: str | None = None,
    ) -> dict | None:
        current = await self.get(uid, owner_id=owner_id)
        if current is None:
            return None
        self._rows[uid] = current | changes
        return self._rows[uid]

    async def delete(
        self,
        uid: str,
        *,
        owner_id: str | None = None,
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
    config_fields=(
        ConfigField(
            key="api_id",
            label="API ID",
            server_managed=True,
            environment_variable="UMEDIA_TELEGRAM_API_ID",
        ),
        ConfigField(
            key="api_hash",
            label="API hash",
            server_managed=True,
            environment_variable="UMEDIA_TELEGRAM_API_HASH",
        ),
        ConfigField(key="channel_id", label="Channel ID"),
        ConfigField(key="session", label="Session", secret=True),
    ),
    capabilities=("list", "read", "write", "delete", "copy"),
)


class FakeRegistry:
    def __init__(self, manifests: dict[str, PluginManifest]) -> None:
        self._manifests = manifests

    def get(self, provider_type: str) -> PluginManifest | None:
        return self._manifests.get(provider_type)


async def passing_connect(  # noqa: RUF029 -- matches the real Connector's async signature
    _manifest: PluginManifest,
    _config: dict,
) -> None:
    return None


def _service(
    repository: FakeRepository | None = None,
    manifests: dict[str, PluginManifest] | None = None,
    server_configs: dict[str, dict[str, str]] | None = None,
) -> tuple[ProviderConnectionService, FakeRepository]:
    repo = repository or FakeRepository()
    return ProviderConnectionService(
        repo,
        FakeCipher(),
        FakeRegistry(manifests or {"local": LOCAL_MANIFEST}),
        passing_connect,
        server_configs=server_configs,
    ), repo


def test_telegram_is_unavailable_without_server_api_credentials() -> None:
    service, _ = _service(manifests={"telegram": TELEGRAM_MANIFEST})

    available, reason = service.availability("telegram")

    assert available is False
    assert reason is not None
    assert "UMEDIA_TELEGRAM_API_ID" in reason
    assert "UMEDIA_TELEGRAM_API_HASH" in reason


@pytest.mark.asyncio
async def test_telegram_connection_is_rejected_without_server_api_credentials() -> None:
    service, repository = _service(manifests={"telegram": TELEGRAM_MANIFEST})

    with pytest.raises(ProviderValidationError, match="UMEDIA_TELEGRAM_API_ID"):
        await service.create(
            provider_type="telegram",
            name="archive-channel",
            config={"channel_id": "-100123", "session": "session"},
            owner_id="user-1",
        )

    assert repository.created is None


@pytest.mark.asyncio
async def test_telegram_creation_injects_server_credentials() -> None:
    service, repository = _service(
        manifests={"telegram": TELEGRAM_MANIFEST},
        server_configs={
            "telegram": {
                "api_id": "12345",
                "api_hash": "server-api-hash",
            },
        },
    )
    connected: dict[str, object] = {}

    async def connect(_manifest: PluginManifest, config: dict) -> None:
        await passing_connect(_manifest, config)
        connected.update(config)

    service._connect = connect
    await service.create(
        provider_type="telegram",
        name="archive-channel",
        config={
            "api_id": "spoofed",
            "api_hash": "spoofed",
            "channel_id": "-100123",
            "session": "kurigram-session",
        },
        owner_id="user-1",
    )

    assert connected == {
        "api_id": "12345",
        "api_hash": "server-api-hash",
        "channel_id": "-100123",
        "session": "kurigram-session",
    }
    assert repository.created is not None


@pytest.mark.asyncio
async def test_create_connection_encrypts_secrets() -> None:
    service, repository = _service()

    result = await service.create(
        provider_type="local",
        name="library",
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
        server_configs={
            "telegram": {"api_id": "12345", "api_hash": "server-api-hash"},
        },
        manifests={"telegram": TELEGRAM_MANIFEST},
    )

    await service.create(
        provider_type="telegram",
        name="channel",
        config={"channel_id": "-100123", "session": "kurigram-session"},
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
            name="library",
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
        name="library",
        config={"root_path": "/x"},
        owner_id="admin-1",
        is_admin=True,
    )

    assert result["owner_id"] == "admin-1"
    assert repository.created["provider_type"] == "local"


def test_connection_usable_by_blocks_local_for_non_admin_owner() -> None:
    local = SimpleNamespace(owner_id="user-1", provider_type="local")
    assert (
        connection_usable_by(
            local,
            actor_user_id="user-1",
            is_admin=False,
        )
        is False
    )
    assert (
        connection_usable_by(
            local,
            actor_user_id="user-1",
            is_admin=True,
        )
        is True
    )


def test_connection_usable_by_blocks_unowned_connections() -> None:
    remote = SimpleNamespace(owner_id="owner-a", provider_type="telegram")
    assert (
        connection_usable_by(
            remote,
            actor_user_id="owner-b",
            is_admin=True,
        )
        is False
    )
    assert (
        connection_usable_by(
            remote,
            actor_user_id="owner-a",
            is_admin=False,
        )
        is True
    )


@pytest.mark.asyncio
async def test_missing_required_provider_field_is_rejected() -> None:
    service, _ = _service()

    with pytest.raises(ProviderValidationError):
        await service.create(
            provider_type="local",
            name="local-disk",
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
            name="xyz",
            config={},
            owner_id="admin-1",
            is_admin=True,
        )


@pytest.mark.asyncio
async def test_update_renames_and_toggles_enabled() -> None:
    service, _ = _service()
    created = await service.create(
        provider_type="local",
        name="library",
        config={"root_path": "/x"},
        owner_id="admin-1",
        is_admin=True,
    )

    updated = await service.update(
        created["uid"],
        owner_id="admin-1",
        name="renamed",
        enabled=False,
    )

    assert updated["name"] == "renamed"
    assert updated["enabled"] is False


@pytest.mark.asyncio
async def test_update_for_other_owner_returns_none() -> None:
    service, _ = _service()
    created = await service.create(
        provider_type="local",
        name="library",
        config={"root_path": "/x"},
        owner_id="admin-1",
        is_admin=True,
    )

    assert (
        await service.update(
            created["uid"],
            owner_id="other-user",
            name="hijack",
        )
        is None
    )


@pytest.mark.asyncio
async def test_update_rejects_an_empty_name() -> None:
    service, _ = _service()
    created = await service.create(
        provider_type="local",
        name="library",
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
        name="library",
        config={"root_path": "/x"},
        owner_id="admin-1",
        is_admin=True,
    )

    result = await service.update(created["uid"], owner_id="admin-1")

    assert result["name"] == "library"


@pytest.mark.asyncio
async def test_update_missing_connection_returns_none() -> None:
    service, _ = _service(manifests={})

    assert await service.update("does-not-exist", name="xyz") is None


@pytest.mark.asyncio
async def test_create_rejects_mirror_structure_without_move_capability() -> None:
    service, _ = _service(
        manifests={"telegram": TELEGRAM_MANIFEST},
        server_configs={
            "telegram": {"api_id": "12345", "api_hash": "server-api-hash"},
        },
    )

    with pytest.raises(ProviderValidationError, match="mirror_structure"):
        await service.create(
            provider_type="telegram",
            name="channel",
            config={"api_id": "1"},
            owner_id="user-1",
            mirror_structure=True,
        )


@pytest.mark.asyncio
async def test_update_rejects_enabling_mirror_structure_without_move() -> None:
    service, _ = _service(
        manifests={"telegram": TELEGRAM_MANIFEST, "local": LOCAL_MANIFEST},
        server_configs={
            "telegram": {"api_id": "12345", "api_hash": "server-api-hash"},
        },
    )
    created = await service.create(
        provider_type="telegram",
        name="channel",
        config={"channel_id": "-100123", "session": "kurigram-session"},
        owner_id="user-1",
        mirror_structure=False,
    )

    with pytest.raises(ProviderValidationError, match="mirror_structure"):
        await service.update(
            created["uid"],
            owner_id="user-1",
            mirror_structure=True,
        )


@pytest.mark.asyncio
async def test_update_can_toggle_flags_when_provider_supports_mirror() -> None:
    service, _ = _service()
    created = await service.create(
        provider_type="local",
        name="library",
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


SFTP_MANIFEST = PluginManifest(
    id="sftp",
    name="SFTP",
    description="Files over SSH.",
    entrypoint=["python", "-m", "plugins.rclone.main"],
    process_id="rclone",
    remote_type="sftp",
    config_fields=(
        ConfigField(key="host", label="Host"),
        ConfigField(key="host_key", label="Server host key", required=False),
    ),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("plugin_code", "error_code"),
    [
        ("HostKeyUnknownError", "host_key_unknown"),
        ("HostKeyMismatchError", "host_key_mismatch"),
    ],
)
async def test_create_surfaces_host_key_confirmation_as_409(
    plugin_code: str, error_code: str,
) -> None:
    """The UI needs the offered key to ask the user to trust it, so a
    host-key answer from the plugin is not flattened into a generic 400."""
    from apps.provider_connections.services import HostKeyConfirmationRequired
    from plugins.client import PluginRPCError

    offered = {
        "host": "sftp.example.com",
        "port": 22,
        "algorithm": "ssh-ed25519",
        "fingerprint": "SHA256:abc",
        "host_key": "ssh-ed25519 AAAA",
    }

    async def connect(_manifest: PluginManifest, _config: dict) -> None:  # noqa: RUF029
        raise PluginRPCError("409", status_code=409, code=plugin_code, data=offered)

    service, repository = _service(manifests={"sftp": SFTP_MANIFEST})
    service._connect = connect

    with pytest.raises(HostKeyConfirmationRequired) as raised:
        await service.create(
            provider_type="sftp",
            name="nas",
            config={"host": "sftp.example.com"},
            owner_id="user-1",
        )

    assert raised.value.status_code == 409
    assert raised.value.error_code == error_code
    assert raised.value.data["host_key"] == offered
    assert repository.created is None


async def _create_local(service: ProviderConnectionService, name: str, owner: str = "admin-1"):  # noqa: ANN202
    return await service.create(
        provider_type="local",
        name=name,
        config={"root_path": "/x"},
        owner_id=owner,
        is_admin=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["My NAS", "", "umedia", "my.nas", "my--nas"])
async def test_create_rejects_a_name_that_is_not_a_valid_bucket_name(name: str) -> None:
    service, repository = _service()

    with pytest.raises(ProviderValidationError) as raised:
        await _create_local(service, name)

    assert raised.value.error_code == "invalid_connection_name"
    assert repository.created is None


@pytest.mark.asyncio
async def test_create_rejects_a_name_the_owner_already_uses() -> None:
    service, _ = _service()
    await _create_local(service, "media")

    with pytest.raises(ProviderValidationError, match="already"):
        await _create_local(service, "media")

    # Names are buckets in each user's own S3 namespace, so another
    # owner may reuse it.
    other = await _create_local(service, "media", owner="admin-2")
    assert other["name"] == "media"


@pytest.mark.asyncio
async def test_create_trims_surrounding_whitespace_before_validating() -> None:
    service, repository = _service()
    await _create_local(service, "  media  ")
    assert repository.created["name"] == "media"


@pytest.mark.asyncio
async def test_rename_applies_the_same_rules_and_allows_keeping_the_name() -> None:
    service, _ = _service()
    first = await _create_local(service, "media")
    second = await _create_local(service, "backup")

    with pytest.raises(ProviderValidationError, match="already"):
        await service.update(second["uid"], owner_id="admin-1", name="media")
    with pytest.raises(ProviderValidationError) as raised:
        await service.update(second["uid"], owner_id="admin-1", name="Back Up")
    assert raised.value.error_code == "invalid_connection_name"

    same = await service.update(first["uid"], owner_id="admin-1", name="media")
    assert same["name"] == "media"
    renamed = await service.update(second["uid"], owner_id="admin-1", name="archive")
    assert renamed["name"] == "archive"
