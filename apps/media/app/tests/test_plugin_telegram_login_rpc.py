from pathlib import Path
from typing import cast

import httpx
import pytest
from fastapi import FastAPI

from plugins.client import PluginClient
from plugins.sdk import PluginBackend, TelegramLoginPluginBackend, create_plugin_app


class LoginBackend(TelegramLoginPluginBackend):
    def __init__(self) -> None:
        self.login_id: str | None = None
        self.password_requested = False

    async def login_start(
        self,
        config: dict[str, object],
        login_id: str,
        phone: str,
        channel_ref: str,
    ) -> dict[str, str]:
        self.login_id = login_id
        return {"step": "code"}

    async def login_code(
        self,
        config: dict[str, object],
        login_id: str,
        code: str,
    ) -> dict[str, str]:
        assert login_id == self.login_id
        return {"step": "password"}

    async def login_password(
        self,
        config: dict[str, object],
        login_id: str,
        password: str,
    ) -> dict[str, str]:
        assert login_id == self.login_id
        self.password_requested = True
        return {"step": "complete"}

    async def login_cancel(self, login_id: str) -> None:
        assert login_id == self.login_id


class TimeoutCapturingASGITransport(httpx.ASGITransport):
    def __init__(self, app: FastAPI) -> None:
        super().__init__(app=app)
        self.read_timeouts: list[float | None] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        timeout = request.extensions.get("timeout")
        read_timeout = timeout.get("read") if isinstance(timeout, dict) else None
        self.read_timeouts.append(
            float(read_timeout) if isinstance(read_timeout, (int, float)) else None,
        )
        return await super().handle_async_request(request)


@pytest.mark.asyncio
async def test_telegram_login_keeps_same_id_across_code_and_password_steps() -> None:
    backend = LoginBackend()
    app = create_plugin_app(cast(PluginBackend, backend))
    transport = TimeoutCapturingASGITransport(app)

    class ASGIPluginClient(PluginClient):
        def _http_client(self) -> httpx.AsyncClient:
            return httpx.AsyncClient(
                transport=transport,
                base_url="http://plugin",
                timeout=self._timeout,
            )

    client = ASGIPluginClient(Path("unused.sock"))

    started = await client.telegram_login_start(
        {},
        login_id="telegram-login-123",
        phone="+10000000000",
        channel_ref="@channel",
    )
    code_step = await client.telegram_login_step(
        {},
        login_id="telegram-login-123",
        step="code",
        value="12345",
    )
    password_step = await client.telegram_login_step(
        {},
        login_id="telegram-login-123",
        step="password",
        value="two-factor-password",
    )

    assert started == {"step": "code"}
    assert code_step == {"step": "password"}
    assert password_step == {"step": "complete"}
    assert backend.password_requested
    assert transport.read_timeouts == [60.0, 60.0, 60.0]
    await client.telegram_login_cancel("telegram-login-123")
