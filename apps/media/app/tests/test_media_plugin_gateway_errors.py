"""MediaPluginGateway maps a plugin's 404 to `ResourceNotFoundError`.

`MediaFileService` treats "already gone at the provider" as success when
deleting (`except ResourceNotFoundError`). The plugin client raises
`PluginRPCError(status_code=404)`, so without this translation a
permanent delete of a folder that no longer exists remotely failed with
502 forever (seen on the preview: Dropbox folder `Works`).
"""

from types import SimpleNamespace

import pytest

from apps.media_files.plugin_gateway import MediaPluginGateway
from plugins.client import PluginClient, PluginRPCError
from plugins.contracts import ResourceNotFoundError
from plugins.manifest import PluginManifest

MANIFEST = PluginManifest(
    id="dropbox",
    name="Dropbox",
    description="",
    entrypoint=["python"],
    process_id="rclone",
    remote_type="dropbox",
)


class _Connections:
    async def get(self, uid: str) -> SimpleNamespace:
        return SimpleNamespace(
            uid=uid, enabled=True, provider_type="dropbox", encrypted_config="x",
        )


def _gateway() -> MediaPluginGateway:
    return MediaPluginGateway(
        _Connections(),
        SimpleNamespace(get=lambda _: MANIFEST),
        SimpleNamespace(socket_path=lambda _: "/nonexistent.sock"),
        SimpleNamespace(decrypt_json=lambda _: {}),
    )


@pytest.mark.asyncio
async def test_delete_of_a_resource_missing_at_the_provider_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def missing(*_: object, **__: object) -> None:  # noqa: RUF029
        raise PluginRPCError("returned 404", status_code=404)

    monkeypatch.setattr(PluginClient, "delete_resource", missing)

    with pytest.raises(ResourceNotFoundError):
        await _gateway().delete_resource("connection-1", "Works")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [None, 400, 500, 502])
async def test_other_delete_failures_still_raise(
    monkeypatch: pytest.MonkeyPatch, status: int | None,
) -> None:
    async def broken(*_: object, **__: object) -> None:  # noqa: RUF029
        raise PluginRPCError("boom", status_code=status)

    monkeypatch.setattr(PluginClient, "delete_resource", broken)

    with pytest.raises(PluginRPCError):
        await _gateway().delete_resource("connection-1", "Works")
