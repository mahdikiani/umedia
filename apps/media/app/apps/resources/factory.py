"""Builds a `ResourceService` from `app.state`.

Mirrors `apps.provider_connections.routes._repository`'s pattern: nothing
here is a singleton (a fresh `ResourceRepository`/`ProviderConnectionRepository`
per call is cheap -- they only hold a session factory), so callers just get
a fully wired service backed by the real SQL repository and the real
subprocess plugin gateway.
"""

from typing import Any, Protocol

from fastapi import Request

from apps.provider_connections.repository import ProviderConnectionRepository

from .plugin_gateway import PluginResourceGateway
from .repository import ResourceRepository
from .services import ResourceService


class _AppStateProtocol(Protocol):
    """The subset of `app.state` this factory reads -- `Request.app.state`
    and a plain `FastAPI.state` (no request in scope, e.g. a background
    task) both satisfy it."""

    session_factory: Any
    plugin_registry: Any
    plugin_process_manager: Any
    credential_cipher: Any


def build_resource_service_from_state(state: _AppStateProtocol) -> ResourceService:
    repository = ResourceRepository(state.session_factory)
    connections = ProviderConnectionRepository(state.session_factory)
    gateway = PluginResourceGateway(
        connections,
        state.plugin_registry,
        state.plugin_process_manager,
        state.credential_cipher,
    )
    return ResourceService(repository, gateway)


def build_resource_service(request: Request) -> ResourceService:
    """The per-request form of `build_resource_service_from_state` --
    what every route uses. `apps.resources.uploads`'s tus completion
    hook runs outside any request (a detached background task), so it
    calls the state-based factory directly instead."""
    return build_resource_service_from_state(request.app.state)
