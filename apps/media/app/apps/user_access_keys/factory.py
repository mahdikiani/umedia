"""Builds a `UserAccessKeyService` from `app.state` -- same pattern as
`apps/media_files/factory.py`: nothing here is a singleton, the
repository only holds a session factory."""

from typing import Any, Protocol

from .repository import UserAccessKeyRepository
from .services import UserAccessKeyService


class _AppStateProtocol(Protocol):
    session_factory: Any
    credential_cipher: Any


def build_user_access_key_service_from_state(
    state: _AppStateProtocol,
) -> UserAccessKeyService:
    return UserAccessKeyService(
        UserAccessKeyRepository(state.session_factory),
        state.credential_cipher,
    )
