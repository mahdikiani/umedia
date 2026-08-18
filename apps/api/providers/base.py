"""Async storage provider contract."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ProviderEntry:
    """Provider-neutral remote object metadata."""

    remote_id: str
    path: str
    name: str
    size: int | None
    is_directory: bool
    modified_at: str | None = None
    etag: str | None = None


class StorageProvider(Protocol):
    """Operations implemented by storage adapters."""

    async def test_connection(self) -> None: ...

    async def list(self, path: str) -> list[ProviderEntry]: ...

    async def stat(self, path: str) -> ProviderEntry: ...

    def read(self, path: str) -> AsyncIterator[bytes]: ...

    async def write(
        self,
        path: str,
        content: AsyncIterator[bytes],
    ) -> ProviderEntry: ...

    async def delete(self, path: str) -> None: ...

    async def move(self, source: str, target: str) -> None: ...

    async def copy(self, source: str, target: str) -> None: ...
