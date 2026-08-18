from pathlib import Path

import pytest

from providers.local import LocalStorageProvider


@pytest.mark.asyncio
async def test_local_provider_accepts_directory_inside_storage_root(
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "storage"
    library = storage_root / "library"
    library.mkdir(parents=True)

    provider = LocalStorageProvider(
        {"root_path": str(library)},
        allowed_root=storage_root,
    )

    await provider.test_connection()


@pytest.mark.asyncio
async def test_local_provider_rejects_path_outside_storage_root(
    tmp_path: Path,
) -> None:
    provider = LocalStorageProvider(
        {"root_path": str(tmp_path / "outside")},
        allowed_root=tmp_path / "storage",
    )

    with pytest.raises(ValueError, match="inside"):
        await provider.test_connection()
