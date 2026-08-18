"""ASGI entrypoint."""

from server.server import create_application

app = create_application()

__all__ = ["app"]
