import asyncio
import logging
import os
import sys
from pathlib import Path

import uvicorn

from server.server import app

__all__ = ["app"]

#: On SIGTERM uvicorn stops accepting connections and gives open requests
#: (a download in progress) this long to finish, then runs the app's
#: lifespan shutdown (background jobs get 10s more, then the plugin
#: processes up to 5s, stopped together). `stop_grace_period` in the
#: compose files covers all of it, so Docker does not SIGKILL a clean
#: shutdown halfway through.
GRACEFUL_SHUTDOWN_SECONDS = 15


async def main() -> None:
    module = Path(__file__).stem
    config = uvicorn.Config(
        f"{module}:app",
        host="0.0.0.0",  # noqa: S104
        # Not UMEDIA_PORT: Docker links and Kubernetes services set
        # <NAME>_PORT=tcp://ip:port for a service named "umedia".
        port=int(os.getenv("UMEDIA_LISTEN_PORT", "8000")),
        access_log=True,
        workers=1,
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
    )
    server = uvicorn.Server(config)
    # uvicorn handles SIGINT/SIGTERM itself and shuts down gracefully; this
    # just waits for it. A second handler here used to cancel the server
    # task on the signal, cutting off Starlette's lifespan mid-shutdown
    # (a CancelledError at deploy time, GlitchTip UMEDIA-5).
    await server.serve()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        logging.exception("Unexpected exception occurred")
        sys.exit(1)
