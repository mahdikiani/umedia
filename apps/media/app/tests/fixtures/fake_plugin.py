"""Minimal fake plugin binary, for PluginProcessManager tests only.

Usage: python fake_plugin.py [--crash-after=SECONDS] <socket_path>

The socket path is always the *last* argument -- PluginProcessManager
appends it to whatever command a manifest declares (see
plugins/process_manager.py's `_spawn`), so a real plugin (and this fake
one) must not assume it's in a fixed positional slot relative to its own
flags.

Serves a bare "200 OK" to any request over the given Unix domain socket,
using a tiny raw asyncio server -- process-manager tests exercise spawn/
health/restart/shutdown mechanics and must not depend on the plugin SDK
(P2.6) or the real resource-verb contract (P2.7), neither of which this is
testing.
"""

import asyncio
import os
import sys
from asyncio import IncompleteReadError, StreamReader, StreamWriter


async def _handle(reader: StreamReader, writer: StreamWriter) -> None:
    try:
        await reader.readuntil(b"\r\n\r\n")
    except IncompleteReadError:
        writer.close()
        return
    # Lets tests confirm exactly what environment this process actually
    # received (env isolation, see test_plugin_process_manager.py):
    # - UMEDIA_TEST_MARKER, if explicitly passed via `start(..., env=...)`,
    #   is echoed back verbatim.
    # - Otherwise, report whether UMEDIA_TEST_SHOULD_NOT_LEAK leaked in
    #   from the parent process despite not being explicitly passed.
    if "UMEDIA_TEST_MARKER" in os.environ:
        body = os.environ["UMEDIA_TEST_MARKER"].encode()
    elif "UMEDIA_TEST_SHOULD_NOT_LEAK" in os.environ:
        body = b"leaked"
    else:
        body = b"ok"
    writer.write(
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: text/plain\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
        b"Connection: close\r\n\r\n" + body
    )
    await writer.drain()
    writer.close()


async def main() -> None:
    socket_path = sys.argv[-1]
    crash_after = None
    for arg in sys.argv[1:-1]:
        if arg.startswith("--crash-after="):
            crash_after = float(arg.split("=", 1)[1])

    if os.path.exists(socket_path):
        os.unlink(socket_path)
    server = await asyncio.start_unix_server(_handle, path=socket_path)

    if crash_after is not None:
        await asyncio.sleep(crash_after)
        os._exit(1)

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
