"""Shared fakes and wiring for the MediaFile/StorageObject test suite.

The service tests (test_media_file_service / _import / _mirror) run the
*real* repositories against a throwaway SQLite file -- the repository
protocols involve a join (MediaFile -> primary StorageObject) that an
in-memory fake would just re-implement, drift from, and eventually
contradict. Only the two true boundaries are faked: the plugin gateway
(no subprocess) and the provider-connection lookup (no encrypted config).
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

from plugins.contracts import CreateResourceIn, UpdateResourceIn
from plugins.contracts import Resource as PluginResource

DEFAULT_CAPABILITIES = ("list", "read", "write", "delete", "move", "copy")


@dataclass
class FakeConnection:
    """The slice of `ProviderConnection` MediaFileService reads."""

    uid: str
    name: str = "Fake connection"
    provider_type: str = "fake"
    enabled: bool = True
    import_existing: bool = False
    mirror_structure: bool = False


class FakeConnectionRepository:
    def __init__(self, *connections: FakeConnection) -> None:
        self._connections = {c.uid: c for c in connections}

    def add(self, connection: FakeConnection) -> None:
        self._connections[connection.uid] = connection

    async def get(self, uid: str) -> FakeConnection | None:
        return self._connections.get(uid)


class FakeMediaPluginGateway:
    """In-memory plugin: a flat store of ref -> bytes plus an explicit,
    test-controlled listing tree for import tests. Records every call so
    tests can assert what was (and crucially, was NOT) called."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.calls: list[tuple] = []
        self.fail_create = False
        self.fail_update = False
        #: parent_ref (None = root) -> resources at that level, per connection:
        #: {connection_id: {parent_ref: [PluginResource, ...]}}
        self.listings: dict[str, dict[str | None, list[PluginResource]]] = {}
        self.capabilities_by_connection: dict[str, tuple[str, ...]] = {}
        self._next_id = 1

    async def capabilities(self, provider_connection_id: str) -> tuple[str, ...]:
        return self.capabilities_by_connection.get(
            provider_connection_id, DEFAULT_CAPABILITIES,
        )

    async def list_resources(
        self, provider_connection_id: str, *, parent_id: str | None = None,
    ) -> list[PluginResource]:
        self.calls.append(("list", provider_connection_id, parent_id))
        return list(
            self.listings.get(provider_connection_id, {}).get(parent_id, []),
        )

    async def create_resource(
        self,
        provider_connection_id: str,
        metadata: CreateResourceIn,
        content: bytes,
    ) -> PluginResource:
        self.calls.append(("create", provider_connection_id, metadata.name))
        if self.fail_create:
            raise RuntimeError("simulated plugin crash on create")
        ref = f"ref-{self._next_id}"
        self._next_id += 1
        self.store[ref] = content
        return PluginResource(
            id=ref,
            type=metadata.type,
            name=metadata.name,
            parent_id=metadata.parent_id,
            size=len(content),
            content_type="application/octet-stream",
        )

    async def get_resource(
        self, provider_connection_id: str, content_reference: str,
    ) -> PluginResource:
        self.calls.append(("get", provider_connection_id, content_reference))
        return PluginResource(
            id=content_reference,
            type="file",
            name="whatever",
            size=len(self.store.get(content_reference, b"")),
        )

    async def update_resource(
        self,
        provider_connection_id: str,
        content_reference: str,
        changes: UpdateResourceIn,
        content: bytes | None,
    ) -> PluginResource:
        self.calls.append(
            ("update", provider_connection_id, content_reference,
             changes.name, changes.parent_id),
        )
        if self.fail_update:
            raise RuntimeError("simulated plugin crash on update")
        if content is not None:
            self.store[content_reference] = content
        return PluginResource(
            id=content_reference,
            type="file",
            name=changes.name or "whatever",
            parent_id=changes.parent_id,
            size=len(self.store.get(content_reference, b"")),
        )

    async def delete_resource(
        self, provider_connection_id: str, content_reference: str,
    ) -> None:
        self.calls.append(("delete", provider_connection_id, content_reference))
        self.store.pop(content_reference, None)

    async def read_content(
        self,
        provider_connection_id: str,
        content_reference: str,
        *,
        range_header: str | None,
    ) -> AsyncIterator[bytes]:
        self.calls.append(("read", provider_connection_id, content_reference))
        if content_reference in self.store:
            yield self.store[content_reference]
            return
        resources = self.listings.get(provider_connection_id, {}).values()
        resource = next(
            (
                item
                for listing in resources
                for item in listing
                if item.id == content_reference
            ),
            None,
        )
        # Listing-only imports get stable bytes matching the advertised size.
        yield b"x" * resource.size if resource is not None and resource.size else b""

    def plugin_calls(self, kind: str | None = None) -> list[tuple]:
        if kind is None:
            return self.calls
        return [call for call in self.calls if call[0] == kind]


@dataclass
class Harness:
    """Everything a service-level test needs, wired together."""

    service: object
    files: object
    objects: object
    plugins: FakeMediaPluginGateway
    connections: FakeConnectionRepository
    #: The real `UserAccessKeyService` (throwaway Fernet cipher) --
    #: temporary share links are signed with per-user access keys.
    access_keys: object = field(default=None)
    engine: object = field(default=None)


async def build_harness(tmp_path: Path, *connections: FakeConnection) -> Harness:
    """Real SQLite repositories + fake gateway/connections."""
    from cryptography.fernet import Fernet
    from fastapi_mongo_base.sql.models import BaseEntity

    from apps.media_files.repository import MediaFileRepository
    from apps.media_files.services import MediaFileService
    from apps.storage_objects.repository import StorageObjectRepository
    from apps.user_access_keys.repository import UserAccessKeyRepository
    from apps.user_access_keys.services import UserAccessKeyService
    from server.database import create_engine, create_session_factory
    from utils.crypto import CredentialCipher

    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'media-files.sqlite3'}")
    async with engine.begin() as conn:
        await conn.run_sync(BaseEntity.metadata.create_all)
    session_factory = create_session_factory(engine)

    files = MediaFileRepository(session_factory)
    objects = StorageObjectRepository(session_factory)
    plugins = FakeMediaPluginGateway()
    connection_repo = FakeConnectionRepository(
        *(connections or (FakeConnection(uid="connection-1"),)),
    )
    access_keys = UserAccessKeyService(
        UserAccessKeyRepository(session_factory),
        CredentialCipher(Fernet.generate_key()),
    )
    service = MediaFileService(
        files, objects, plugins, connection_repo,
        access_keys=access_keys,
    )
    return Harness(
        service=service,
        files=files,
        objects=objects,
        plugins=plugins,
        connections=connection_repo,
        access_keys=access_keys,
        engine=engine,
    )
