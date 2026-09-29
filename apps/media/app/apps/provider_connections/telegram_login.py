from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

from fastapi_mongo_base.core.exceptions import BaseHTTPException

from plugins.client import PluginClient, PluginRPCError
from plugins.process_manager import PluginProcessManager

from .services import ProviderConnectionService

LOGIN_TTL_SECONDS = 300
MAX_PENDING_LOGINS = 20


class TelegramLoginError(BaseHTTPException):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(
            status_code=status_code,
            error_code="telegram_login_failed",
            detail=detail,
            message=detail,
        )


@dataclass
class _PendingLogin:
    owner_id: str
    name: str
    channel_ref: str
    import_existing: bool
    mirror_structure: bool
    expires_at: float


class TelegramLoginService:
    def __init__(
        self,
        process_manager: PluginProcessManager,
        *,
        api_id: str,
        api_hash: str,
    ) -> None:
        self._client = PluginClient(process_manager.socket_path("telegram"))
        self._config = {"api_id": api_id, "api_hash": api_hash}
        self._pending: dict[str, _PendingLogin] = {}

    async def shutdown(self) -> None:
        login_ids = list(self._pending)
        self._pending.clear()
        for login_id in login_ids:
            await self._cancel_plugin(login_id)

    async def start(
        self,
        *,
        owner_id: str,
        name: str,
        phone: str,
        channel_ref: str,
        import_existing: bool,
        mirror_structure: bool,
    ) -> dict[str, str]:
        expired = [
            login_id
            for login_id, pending in self._pending.items()
            if pending.expires_at <= time.monotonic()
        ]
        for login_id in expired:
            self._pending.pop(login_id, None)
            await self._cancel_plugin(login_id)
        if not all(str(value).strip() for value in self._config.values()):
            raise TelegramLoginError(422, "Telegram API credentials are not configured")
        if len(self._pending) >= MAX_PENDING_LOGINS:
            raise TelegramLoginError(429, "Too many Telegram login attempts")
        if not phone.strip() or not channel_ref.strip():
            raise TelegramLoginError(
                422,
                "Phone number and channel name or username are required",
            )
        login_id = secrets.token_urlsafe(32)
        try:
            response = await self._client.telegram_login_start(
                self._config,
                login_id=login_id,
                phone=phone.strip(),
                channel_ref=channel_ref.strip(),
            )
        except PluginRPCError as error:
            raise TelegramLoginError(
                400,
                "Telegram could not send a login code",
            ) from error
        self._pending[login_id] = _PendingLogin(
            owner_id=owner_id,
            name=name.strip(),
            channel_ref=channel_ref.strip(),
            import_existing=import_existing,
            mirror_structure=mirror_structure,
            expires_at=time.monotonic() + LOGIN_TTL_SECONDS,
        )
        return {"login_id": login_id, **response}

    def _get(self, owner_id: str, login_id: str) -> _PendingLogin:
        pending = self._pending.get(login_id)
        if pending is None or pending.expires_at <= time.monotonic():
            self._pending.pop(login_id, None)
            raise TelegramLoginError(404, "Telegram login expired; start again")
        if pending.owner_id != owner_id:
            raise TelegramLoginError(404, "Telegram login not found")
        return pending

    async def advance(
        self,
        *,
        owner_id: str,
        login_id: str,
        step: str,
        value: str,
        connections: ProviderConnectionService,
        is_admin: bool,
    ) -> tuple[dict[str, str], object | None]:
        pending = self._get(owner_id, login_id)
        if step not in {"code", "password"}:
            raise TelegramLoginError(422, "Invalid Telegram login step")
        try:
            response = await self._client.telegram_login_step(
                self._config,
                login_id=login_id,
                step=step,
                value=value,
            )
        except PluginRPCError as error:
            if error.status_code == 400 and step == "code":
                detail = (
                    "Telegram login code is invalid or expired. "
                    "Cancel and request a new code."
                )
            elif error.status_code == 400 and step == "password":
                detail = "Telegram two-step verification password is incorrect."
            elif error.status_code == 422:
                detail = (
                    "Telegram could not find that channel. Check its name or "
                    "@username; if you entered a name, make sure you have "
                    "joined the channel."
                )
            else:
                detail = "Telegram login failed. Please try again."
            raise TelegramLoginError(
                error.status_code if error.status_code in {400, 404, 422} else 502,
                detail,
            ) from error

        if response.get("step") != "complete":
            return response, None
        session = response.pop("session", "")
        channel_id = response.pop("channel_id", "")
        if not channel_id:
            self._pending.pop(login_id, None)
            await self._cancel_plugin(login_id)
            raise TelegramLoginError(
                502,
                "Telegram did not return a resolved channel ID",
            )
        self._pending.pop(login_id, None)
        try:
            connection = await connections.create(
                provider_type="telegram",
                name=pending.name,
                config={"channel_id": channel_id, "session": session},
                owner_id=owner_id,
                is_admin=is_admin,
                import_existing=pending.import_existing,
                mirror_structure=pending.mirror_structure,
            )
        except Exception:
            await self._cancel_plugin(login_id)
            raise
        return {"step": "complete"}, connection

    async def cancel(self, *, owner_id: str, login_id: str) -> None:
        self._get(owner_id, login_id)
        self._pending.pop(login_id, None)
        await self._cancel_plugin(login_id)

    async def _cancel_plugin(self, login_id: str) -> None:
        try:
            await self._client.telegram_login_cancel(login_id)
        except PluginRPCError:
            return
