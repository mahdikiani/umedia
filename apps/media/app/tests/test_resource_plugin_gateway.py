"""Integration test for PluginResourceGateway: a real ProviderConnection
row (encrypted config, via the real SQL repository) resolved to a real
spawned `local` plugin subprocess, end to end -- proves the
apps/resources <-> apps/provider_connections <-> plugins chain P4.3 wires
together, not just each piece in isolation.
"""

import sys
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi_mongo_base.sql.models import BaseEntity
from sqlalchemy.ext.asyncio import async_sessionmaker

from apps.provider_connections.models import ProviderConnection  # noqa: F401
from apps.provider_connections.repository import ProviderConnectionRepository
from apps.resources.plugin_gateway import PluginResourceGateway
from plugins.contracts import CreateResourceIn, UpdateResourceIn
from plugins.process_manager import PluginProcessManager
from plugins.registry import PluginRegistry
from server.database import create_engine, create_session_factory
from utils.crypto import CredentialCipher

PLUGINS_DIR = Path(__file__).resolve().parents[1] / "plugins"


@pytest_asyncio.fixture
async def connections(tmp_path: Path) -> AsyncGenerator[ProviderConnectionRepository]:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'gateway-test.sqlite3'}")
    async with engine.begin() as connection:
        await connection.run_sync(BaseEntity.metadata.create_all)
    session_factory: async_sessionmaker = create_session_factory(engine)
    yield ProviderConnectionRepository(session_factory)
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="function")
async def process_manager(tmp_path: Path) -> AsyncGenerator[PluginProcessManager]:
    library = tmp_path / "storage" / "library"
    manager = PluginProcessManager(tmp_path / "sockets")
    await manager.start(
        "local",
        [sys.executable, "-m", "plugins.local.main"],
        env={"UMEDIA_LOCAL_STORAGE_ROOT": str(tmp_path / "storage")},
    )
    library.mkdir(parents=True)
    yield manager
    await manager.stop_all()


@pytest.fixture
def cipher(tmp_path: Path) -> CredentialCipher:
    return CredentialCipher.from_environment(data_dir=tmp_path, env_key=None)


@pytest.fixture
def gateway(
    connections: ProviderConnectionRepository,
    process_manager: PluginProcessManager,
    cipher: CredentialCipher,
) -> PluginResourceGateway:
    registry = PluginRegistry(PLUGINS_DIR)
    return PluginResourceGateway(connections, registry, process_manager, cipher)


@pytest_asyncio.fixture
async def local_connection_id(
    connections: ProviderConnectionRepository,
    cipher: CredentialCipher,
    tmp_path: Path,
) -> str:
    connection = await connections.create({
        "owner_id": "test-owner",
        "provider_type": "local",
        "name": "Test Library",
        "encrypted_config": cipher.encrypt_json(
            {"root_path": str(tmp_path / "storage" / "library")},
        ),
        "status": "configured",
    })
    return connection.uid


@pytest.mark.asyncio
async def test_create_get_update_delete_round_trip(
    gateway: PluginResourceGateway, local_connection_id: str,
) -> None:
    created = await gateway.create_resource(
        local_connection_id,
        CreateResourceIn(name="hello.txt"),
        b"hello, gateway",
    )
    assert created.name == "hello.txt"

    fetched = await gateway.get_resource(local_connection_id, created.id)
    assert fetched.id == created.id

    content = b"".join([
        chunk async for chunk in gateway.read_content(
            local_connection_id, created.id, range_header=None,
        )
    ])
    assert content == b"hello, gateway"

    renamed = await gateway.update_resource(
        local_connection_id, created.id, UpdateResourceIn(name="renamed.txt"), None,
    )
    assert renamed.name == "renamed.txt"

    await gateway.delete_resource(local_connection_id, renamed.id)
    with pytest.raises(Exception):  # noqa: B017 -- PluginRPCError, 404
        await gateway.get_resource(local_connection_id, renamed.id)


@pytest.mark.asyncio
async def test_unknown_connection_id_is_a_validation_error(
    gateway: PluginResourceGateway,
) -> None:
    from apps.resources.errors import ResourceValidationError

    with pytest.raises(ResourceValidationError):
        await gateway.get_resource("does-not-exist", "1")


@pytest.mark.asyncio
async def test_disabled_connection_is_a_validation_error(
    gateway: PluginResourceGateway,
    connections: ProviderConnectionRepository,
    local_connection_id: str,
) -> None:
    from apps.resources.errors import ResourceValidationError

    await connections.update(local_connection_id, {"enabled": False})

    with pytest.raises(ResourceValidationError):
        await gateway.get_resource(local_connection_id, "1")
