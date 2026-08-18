"""Unit tests for LocalBackend, in-process (no subprocess) -- fast checks
for containment/CRUD/range behavior. The full process+SDK+contract
round-trip is tests/test_plugin_local_contract.py.
"""

from pathlib import Path

import pytest

from plugins.contracts import (
    ConnectionFailedError,
    CreateResourceIn,
    ResourceNotFoundError,
    UpdateResourceIn,
)
from plugins.local.backend import LocalBackend


async def _bytes(iterator: object) -> bytes:
    return b"".join([chunk async for chunk in iterator])


@pytest.fixture
def allowed_root(tmp_path: Path) -> Path:
    root = tmp_path / "storage"
    root.mkdir()
    return root


@pytest.fixture
def backend(allowed_root: Path) -> LocalBackend:
    return LocalBackend(allowed_root=allowed_root)


@pytest.fixture
def config(allowed_root: Path) -> dict[str, str]:
    library = allowed_root / "library"
    library.mkdir()
    return {"root_path": str(library)}


@pytest.mark.asyncio
async def test_connect_rejects_a_root_path_outside_the_allowed_root(
    backend: LocalBackend, allowed_root: Path,
) -> None:
    with pytest.raises(ConnectionFailedError, match="inside"):
        await backend.connect({"root_path": str(allowed_root.parent / "outside")})


@pytest.mark.asyncio
async def test_connect_accepts_and_creates_a_directory_inside_the_allowed_root(
    backend: LocalBackend, allowed_root: Path,
) -> None:
    target = allowed_root / "new-library"
    await backend.connect({"root_path": str(target)})

    assert target.is_dir()


@pytest.mark.asyncio
async def test_create_list_get_read_round_trip(
    backend: LocalBackend, config: dict[str, str],
) -> None:
    created = await backend.create_resource(
        config,
        CreateResourceIn(name="hello.txt"),
        content=_aiter([b"hello, ", b"world"]),
    )

    assert created.id == "hello.txt"
    assert created.size == 12
    assert created.content_type == "text/plain"

    listing = await backend.list_resources(config, parent_id=None)
    assert [r.id for r in listing] == ["hello.txt"]
    assert listing[0].content_type == "text/plain"

    fetched = await backend.get_resource(config, "hello.txt")
    assert fetched.size == 12
    assert fetched.content_type == "text/plain"

    content = await _bytes(
        backend.read_content(config, "hello.txt", range_header=None),
    )
    assert content == b"hello, world"


@pytest.mark.asyncio
async def test_range_read_returns_only_the_requested_bytes(
    backend: LocalBackend, config: dict[str, str],
) -> None:
    await backend.create_resource(
        config, CreateResourceIn(name="range.txt"), content=_aiter([b"0123456789"]),
    )

    content = await _bytes(
        backend.read_content(config, "range.txt", range_header="bytes=2-4"),
    )

    assert content == b"234"


@pytest.mark.asyncio
async def test_create_folder_then_nested_file(
    backend: LocalBackend, config: dict[str, str],
) -> None:
    await backend.create_resource(
        config, CreateResourceIn(name="sub", type="folder"), content=_aiter([]),
    )
    nested = await backend.create_resource(
        config,
        CreateResourceIn(name="nested.txt", parent_id="sub"),
        content=_aiter([b"x"]),
    )

    assert nested.id == "sub/nested.txt"
    assert nested.parent_id == "sub"

    top_level = await backend.list_resources(config, parent_id=None)
    assert [r.id for r in top_level] == ["sub"]
    assert top_level[0].type == "folder"
    assert top_level[0].content_type == "inode/directory"

    nested_listing = await backend.list_resources(config, parent_id="sub")
    assert [r.id for r in nested_listing] == ["sub/nested.txt"]
    assert nested_listing[0].content_type == "text/plain"


@pytest.mark.asyncio
async def test_list_resources_detects_pdf_and_png_content_types(
    backend: LocalBackend, config: dict[str, str],
) -> None:
    root = Path(config["root_path"])
    (root / "document.pdf").write_bytes(b"%PDF-1.4\n")
    (root / "image.png").write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR",
    )

    listing = await backend.list_resources(config, parent_id=None)

    content_types = {resource.name: resource.content_type for resource in listing}
    assert content_types == {
        "document.pdf": "application/pdf",
        "image.png": "image/png",
    }


@pytest.mark.asyncio
async def test_path_traversal_is_rejected_as_not_found(
    backend: LocalBackend, config: dict[str, str],
) -> None:
    with pytest.raises(ResourceNotFoundError):
        await backend.get_resource(config, "../../etc/passwd")


@pytest.mark.asyncio
async def test_update_renames_and_moves(
    backend: LocalBackend, config: dict[str, str],
) -> None:
    await backend.create_resource(
        config, CreateResourceIn(name="a.txt"), content=_aiter([b"content"]),
    )
    await backend.create_resource(
        config, CreateResourceIn(name="dest", type="folder"), content=_aiter([]),
    )

    moved = await backend.update_resource(
        config,
        "a.txt",
        UpdateResourceIn(name="b.txt", parent_id="dest"),
        content=None,
    )

    assert moved.id == "dest/b.txt"
    with pytest.raises(ResourceNotFoundError):
        await backend.get_resource(config, "a.txt")


@pytest.mark.asyncio
async def test_delete_removes_a_file_and_a_nonempty_folder(
    backend: LocalBackend, config: dict[str, str],
) -> None:
    await backend.create_resource(
        config, CreateResourceIn(name="dir", type="folder"), content=_aiter([]),
    )
    await backend.create_resource(
        config,
        CreateResourceIn(name="inner.txt", parent_id="dir"),
        content=_aiter([b"x"]),
    )

    await backend.delete_resource(config, "dir")

    with pytest.raises(ResourceNotFoundError):
        await backend.get_resource(config, "dir")


@pytest.mark.asyncio
async def test_delete_missing_resource_raises_not_found(
    backend: LocalBackend, config: dict[str, str],
) -> None:
    with pytest.raises(ResourceNotFoundError):
        await backend.delete_resource(config, "does-not-exist.txt")


async def _aiter(chunks: list[bytes]) -> object:  # noqa: RUF029 -- async iterator, not async I/O
    for chunk in chunks:
        yield chunk
