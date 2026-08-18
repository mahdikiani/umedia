"""Builds an `S3ObjectService` from `app.state` -- same pattern as
`apps/media_files/factory.py`: nothing here is a singleton, the wrapped
media service's repositories only hold a session factory."""

from typing import Any, Protocol

from apps.media_files.factory import build_media_file_service_from_state
from apps.provider_connections.repository import ProviderConnectionRepository

from .services import S3ObjectService


class _AppStateProtocol(Protocol):
    """What `build_media_file_service_from_state` needs off `app.state`."""

    session_factory: Any
    plugin_registry: Any
    plugin_process_manager: Any
    credential_cipher: Any


def build_s3_service(
    state: _AppStateProtocol, *, user_id: str,
) -> S3ObjectService:
    """The gateway service scoped to one authenticated key owner."""
    return S3ObjectService(
        user_id=user_id,
        media_files=build_media_file_service_from_state(state),
        connections=ProviderConnectionRepository(state.session_factory),
    )
