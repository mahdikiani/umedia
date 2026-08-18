"""In-memory reference plugin, built on the plugin SDK.

Exists to prove the SDK + contract test suite work end-to-end over a real
Unix socket *before* any real provider plugin (Phase 3) does -- see
docs/09-tasks.md P2.7. Not a real provider: resources live in a plain
dict, reset every process start.
"""

import asyncio
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from plugins.contracts import (
    ConnectionFailedError,
    CreateResourceIn,
    Resource,
    ResourceNotFoundError,
    UpdateResourceIn,
)
from plugins.sdk import PluginBackend, run_plugin


class InMemoryBackend(PluginBackend):
    def __init__(self) -> None:
        self._resources: dict[str, Resource] = {}
        self._content: dict[str, bytes] = {}

    async def connect(self, config: dict[str, Any]) -> None:
        if not config.get("accept", True):
            raise ConnectionFailedError("config rejected by test fixture")

    async def list_resources(
        self,
        config: dict[str, Any],
        *,
        parent_id: str | None,
    ) -> list[Resource]:
        return [r for r in self._resources.values() if r.parent_id == parent_id]

    async def get_resource(
        self,
        config: dict[str, Any],
        resource_id: str,
    ) -> Resource:
        resource = self._resources.get(resource_id)
        if resource is None:
            raise ResourceNotFoundError(resource_id)
        return resource

    async def read_content(
        self,
        config: dict[str, Any],
        resource_id: str,
        *,
        range_header: str | None,
    ) -> AsyncIterator[bytes]:
        if resource_id not in self._resources:
            raise ResourceNotFoundError(resource_id)
        yield self._content.get(resource_id, b"")

    async def create_resource(
        self,
        config: dict[str, Any],
        metadata: CreateResourceIn,
        *,
        content: AsyncIterator[bytes],
    ) -> Resource:
        body = b"".join([chunk async for chunk in content])
        resource_id = str(uuid.uuid4())
        resource = Resource(
            id=resource_id,
            type=metadata.type,
            name=metadata.name,
            parent_id=metadata.parent_id,
            size=len(body),
        )
        self._resources[resource_id] = resource
        self._content[resource_id] = body
        return resource

    async def update_resource(
        self,
        config: dict[str, Any],
        resource_id: str,
        changes: UpdateResourceIn,
        *,
        content: AsyncIterator[bytes] | None,
    ) -> Resource:
        resource = self._resources.get(resource_id)
        if resource is None:
            raise ResourceNotFoundError(resource_id)
        updated = resource.model_copy(
            update={
                key: value
                for key, value in (
                    ("name", changes.name),
                    ("parent_id", changes.parent_id),
                )
                if value is not None
            },
        )
        if content is not None:
            body = b"".join([chunk async for chunk in content])
            self._content[resource_id] = body
            updated = updated.model_copy(update={"size": len(body)})
        self._resources[resource_id] = updated
        return updated

    async def delete_resource(
        self,
        config: dict[str, Any],
        resource_id: str,
    ) -> None:
        if resource_id not in self._resources:
            raise ResourceNotFoundError(resource_id)
        del self._resources[resource_id]
        self._content.pop(resource_id, None)


if __name__ == "__main__":
    asyncio.run(run_plugin(InMemoryBackend()))
