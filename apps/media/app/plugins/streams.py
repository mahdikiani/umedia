"""Start a byte stream before committing to a success response.

A lazy async generator handed to `StreamingResponse` only runs once the
200 status and headers are on the wire. If the provider read fails before
the first byte (a file rclone cannot `cat`), the client gets a 200 with a
truncated, empty body and the server logs "response already started"
(GlitchTip UMEDIA-2). Pulling the first chunk up front lets that failure
raise while the caller can still answer with a real error status.

Shared by the plugin SDK (plugin side) and the media layer (core side),
like `contracts.py`.
"""

import contextlib
from collections.abc import AsyncIterator


async def prime_stream(stream: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    """Await `stream`'s first chunk now; return an iterator over all of it.

    An error before the first chunk raises from this call (and closes
    `stream`). Errors after it still surface mid-body, as before. A caller
    that gives up on the result before reading it (a failed check after
    priming) must `close_stream` it, or the source -- an `rclone cat`
    process, an open plugin response -- stays open until garbage collection.
    """
    iterator = aiter(stream)
    try:
        first = await anext(iterator)
    except StopAsyncIteration:
        return _empty()
    except BaseException:
        await close_stream(iterator)
        raise
    return _PrimedStream(first, iterator)


class _PrimedStream:
    """The first chunk, then the rest of `iterator`.

    A class rather than an async generator: `aclose()` on a generator that
    never started skips its body, so it could not close the source.
    """

    def __init__(self, first: bytes, iterator: AsyncIterator[bytes]) -> None:
        self._first: bytes | None = first
        self._iterator = iterator

    def __aiter__(self) -> "_PrimedStream":
        return self

    async def __anext__(self) -> bytes:
        if self._first is not None:
            chunk, self._first = self._first, None
            return chunk
        return await anext(self._iterator)

    async def aclose(self) -> None:
        self._first = None
        await close_stream(self._iterator)


async def _empty() -> AsyncIterator[bytes]:  # noqa: RUF029 -- empty async generator
    return
    yield  # pragma: no cover -- makes this an async generator


async def close_stream(stream: AsyncIterator[bytes]) -> None:
    """Close `stream` if it can be closed, ignoring errors while closing."""
    aclose = getattr(stream, "aclose", None)
    if aclose is not None:
        with contextlib.suppress(Exception):
            await aclose()
