from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import pytest

from apps.provider_connections.services import ProviderConnectionService
from apps.provider_connections.telegram_login import (
    TelegramLoginError,
    TelegramLoginService,
)
from plugins.client import PluginRPCError


class FakePluginClient:
    def __init__(self, _socket_path: Path) -> None:
        self.login_calls: list[tuple[str, str]] = []
        self.cancelled: list[str] = []

    async def telegram_login_start(
        self,
        _config: dict[str, str],
        **_kwargs: str,
    ) -> dict[str, str]:
        return {"step": "code"}

    async def telegram_login_step(
        self,
        _config: dict[str, str],
        *,
        login_id: str,
        step: str,
        value: str,
    ) -> dict[str, str]:
        self.login_calls.append((step, value))
        if value == "bad":
            return {"step": "password"}
        return {
            "step": "complete",
            "session": "private-session",
            "channel_id": "-100123",
        }

    async def telegram_login_cancel(self, login_id: str) -> None:
        self.cancelled.append(login_id)


class FakeProcessManager:
    def socket_path(self, _plugin_id: str) -> Path:
        return Path("telegram.sock")


class FakeConnections:
    def __init__(self) -> None:
        self.data: dict[str, object] | None = None

    async def create(
        self,
        *,
        provider_type: str,
        name: str,
        config: dict[str, str],
        owner_id: str,
        is_admin: bool,
        import_existing: bool,
        mirror_structure: bool,
    ) -> object:
        self.data = {
            "provider_type": provider_type,
            "name": name,
            "config": config,
            "owner_id": owner_id,
            "is_admin": is_admin,
            "import_existing": import_existing,
            "mirror_structure": mirror_structure,
        }
        return SimpleNamespace(uid="connection-1")


@pytest.mark.asyncio
async def test_login_flow_binds_owner_and_persists_session_only_in_core() -> None:
    with patch(
        "apps.provider_connections.telegram_login.PluginClient",
        FakePluginClient,
    ):
        service = TelegramLoginService(
            FakeProcessManager(),
            api_id="1",
            api_hash="secret-hash",
        )
        started = await service.start(
            owner_id="user-1",
            name="Archive",
            phone="+1234567890",
            channel_ref="@my_channel",
            import_existing=False,
            mirror_structure=False,
        )
        login_id = started["login_id"]
        connections = FakeConnections()

        with pytest.raises(TelegramLoginError) as error:
            await service.advance(
                owner_id="user-2",
                login_id=login_id,
                step="code",
                value="12345",
                connections=cast(ProviderConnectionService, connections),
                is_admin=False,
            )
        assert error.value.status_code == 404

        response, connection = await service.advance(
            owner_id="user-1",
            login_id=login_id,
            step="code",
            value="12345",
            connections=cast(ProviderConnectionService, connections),
            is_admin=False,
        )

    assert response == {"step": "complete"}
    assert connection.uid == "connection-1"
    assert connections.data is not None
    assert connections.data["config"] == {
        "channel_id": "-100123",
        "session": "private-session",
    }
    assert connections.data["owner_id"] == "user-1"


@pytest.mark.parametrize(
    ("step", "message"),
    [
        (
            "code",
            "Telegram login code is invalid or expired. Cancel and request a new code.",
        ),
        ("password", "Telegram two-step verification password is incorrect."),
    ],
)
async def test_login_step_reports_which_telegram_credential_was_rejected(
    step: str,
    message: str,
) -> None:
    class RejectingPluginClient(FakePluginClient):
        async def telegram_login_step(
            self,
            _config: dict[str, str],
            *,
            login_id: str,
            step: str,
            value: str,
        ) -> dict[str, str]:
            raise PluginRPCError("rejected", status_code=400)

    with patch(
        "apps.provider_connections.telegram_login.PluginClient",
        RejectingPluginClient,
    ):
        service = TelegramLoginService(
            FakeProcessManager(),
            api_id="1",
            api_hash="secret-hash",
        )
        started = await service.start(
            owner_id="user-1",
            name="Archive",
            phone="+1234567890",
            channel_ref="@my_channel",
            import_existing=False,
            mirror_structure=False,
        )

        with pytest.raises(TelegramLoginError) as error:
            await service.advance(
                owner_id="user-1",
                login_id=started["login_id"],
                step=step,
                value="rejected-value",
                connections=cast(ProviderConnectionService, FakeConnections()),
                is_admin=False,
            )

    assert error.value.detail == message
