"""Local filesystem storage adapter."""

from pathlib import Path
from typing import Any


class LocalStorageProvider:
    """Validate and access a directory mounted under the storage root."""

    def __init__(self, config: dict[str, Any], *, allowed_root: Path) -> None:
        self._root = Path(str(config["root_path"]))
        self._allowed_root = allowed_root

    async def test_connection(self) -> None:
        """Verify path containment and read/write access."""
        allowed = self._allowed_root.resolve()
        root = self._root.resolve()
        if not root.is_relative_to(allowed):
            raise ValueError(f"Local path must be inside {allowed}")
        root.mkdir(parents=True, exist_ok=True)
        if not root.is_dir():
            raise ValueError("Local storage path is not a directory")
        probe = root / ".umedia-connection-test"
        probe.write_bytes(b"umedia")
        probe.unlink()

