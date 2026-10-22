"""The plugin SDK turns contract errors into statuses the core acts on."""

from collections.abc import AsyncIterator

import httpx
import pytest

from plugins.contracts import (
    ProviderAuthError,
    ResourceUnavailableError,
    encode_header_json,
)
from plugins.sdk import PluginBackend, create_plugin_app


class _ListFails(PluginBackend):
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def connect(self, config: dict) -> None: ...

    async def list_resources(self, config: dict, *, parent_id: str | None) -> list:
        raise self._error

    async def get_resource(self, config: dict, resource_id: str) -> object: ...

    def read_content(
        self, config: dict, resource_id: str, *, range_header: str | None,
    ) -> AsyncIterator[bytes]:
        raise NotImplementedError

    async def create_resource(
        self, config: dict, metadata: object, *, content: object,
    ) -> object: ...

    async def update_resource(
        self, config: dict, resource_id: str, changes: object, *, content: object,
    ) -> object: ...

    async def delete_resource(self, config: dict, resource_id: str) -> None: ...


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (ResourceUnavailableError("invalidResourceId"), 422),
        (ProviderAuthError("invalid_grant"), 401),
    ],
)
async def test_list_errors_reach_the_core_with_their_status(
    error: Exception, status: int,
) -> None:
    app = create_plugin_app(_ListFails(error))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://plugin",
    ) as client:
        response = await client.get(
            "/resources",
            params={"parent_id": "Personal Vault"},
            headers={"X-Umedia-Connection-Config": encode_header_json({})},
        )
    assert response.status_code == status
    assert response.json()["code"] == type(error).__name__
