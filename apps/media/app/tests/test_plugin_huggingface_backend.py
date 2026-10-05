"""Hugging Face Buckets (Xet) plugin: unit tests against an in-memory
bucket, plus the shared contract suite over a real plugin socket."""

import sys
from pathlib import Path

import pytest
import pytest_asyncio

from plugins.client import PluginClient, PluginRPCError
from plugins.contracts import (
    ConnectionFailedError,
    CreateResourceIn,
    ResourceNotFoundError,
    UpdateResourceIn,
)
from plugins.huggingface.backend import FOLDER_MARKER, HuggingFaceBackend
from plugins.process_manager import PluginProcessManager

from .fixtures.fake_hf_bucket import FakeBucketApi
from .plugin_contract import run_contract_suite

CONFIG = {"bucket": "me/media", "token": "hf_test"}
FAKE_PLUGIN = Path(__file__).parent / "fixtures" / "huggingface_fake_plugin.py"


async def _body(data: bytes):  # noqa: ANN202, RUF029 -- async generator
    yield data


@pytest.fixture
def fake() -> FakeBucketApi:
    return FakeBucketApi()


@pytest.fixture
def backend(fake: FakeBucketApi) -> HuggingFaceBackend:
    return HuggingFaceBackend(api_factory=lambda **_: fake, transport=fake.transport())


async def _read(
    backend: HuggingFaceBackend, rid: str, range_header: str | None = None
) -> bytes:
    chunks = [
        c async for c in backend.read_content(CONFIG, rid, range_header=range_header)
    ]
    return b"".join(chunks)


@pytest.mark.asyncio
async def test_connect_requires_bucket_id_and_token(
    backend: HuggingFaceBackend,
) -> None:
    with pytest.raises(ConnectionFailedError, match="bucket"):
        await backend.connect({"token": "t"})
    with pytest.raises(ConnectionFailedError, match="namespace/name"):
        await backend.connect({"bucket": "no-namespace", "token": "t"})
    with pytest.raises(ConnectionFailedError, match="token"):
        await backend.connect({"bucket": "me/media"})


@pytest.mark.asyncio
async def test_connect_accepts_hf_uri_and_rejects_unknown_bucket(
    backend: HuggingFaceBackend,
) -> None:
    await backend.connect({"bucket": "hf://buckets/me/media", "token": "t"})
    with pytest.raises(ConnectionFailedError):
        await backend.connect({"bucket": "me/missing", "token": "t"})


@pytest.mark.asyncio
async def test_listing_is_path_component_aware_not_lexical(
    backend: HuggingFaceBackend,
    fake: FakeBucketApi,
) -> None:
    fake.objects = {"logs/a.txt": b"a", "logs_old/b.txt": b"b", "top.bin": b"t"}

    root = {r.id: r.type for r in await backend.list_resources(CONFIG, parent_id=None)}
    assert root == {"logs": "folder", "logs_old": "folder", "top.bin": "file"}

    logs = await backend.list_resources(CONFIG, parent_id="logs")
    assert [(r.id, r.parent_id, r.size) for r in logs] == [("logs/a.txt", "logs", 1)]


@pytest.mark.asyncio
async def test_empty_folder_persists_via_hidden_marker(
    backend: HuggingFaceBackend,
    fake: FakeBucketApi,
) -> None:
    folder = await backend.create_resource(
        CONFIG,
        CreateResourceIn(name="album", type="folder"),
        content=_body(b""),
    )

    assert folder.type == "folder"
    assert f"album/{FOLDER_MARKER}" in fake.objects
    assert await backend.list_resources(CONFIG, parent_id="album") == []
    assert (await backend.get_resource(CONFIG, "album")).type == "folder"


@pytest.mark.asyncio
async def test_read_honors_range(
    backend: HuggingFaceBackend, fake: FakeBucketApi
) -> None:
    fake.objects["clip.mp4"] = b"0123456789"

    assert await _read(backend, "clip.mp4") == b"0123456789"
    assert await _read(backend, "clip.mp4", "bytes=2-5") == b"2345"
    with pytest.raises(ResourceNotFoundError):
        await _read(backend, "missing.mp4")


@pytest.mark.asyncio
async def test_rename_folder_copies_server_side_and_removes_old_paths(
    backend: HuggingFaceBackend,
    fake: FakeBucketApi,
) -> None:
    fake.objects = {"a/x.txt": b"x", "a/sub/y.txt": b"y"}

    moved = await backend.update_resource(
        CONFIG,
        "a",
        UpdateResourceIn(name="b"),
        content=None,
    )

    assert moved.id == "b"
    assert fake.objects == {"b/x.txt": b"x", "b/sub/y.txt": b"y"}


@pytest.mark.asyncio
async def test_overwrite_content_and_delete_folder(
    backend: HuggingFaceBackend,
    fake: FakeBucketApi,
) -> None:
    fake.objects = {"d/f.txt": b"old", "d/g.txt": b"g", "keep.txt": b"k"}

    await backend.update_resource(
        CONFIG,
        "d/f.txt",
        UpdateResourceIn(overwrite_content=True),
        content=_body(b"new"),
    )
    assert fake.objects["d/f.txt"] == b"new"

    await backend.delete_resource(CONFIG, "d")
    assert fake.objects == {"keep.txt": b"k"}
    with pytest.raises(ResourceNotFoundError):
        await backend.delete_resource(CONFIG, "d")


@pytest.mark.asyncio
async def test_rejects_traversal_and_marker_names(backend: HuggingFaceBackend) -> None:
    for name in ["../x", "a/b", FOLDER_MARKER, ""]:
        with pytest.raises(ConnectionFailedError):
            await backend.create_resource(
                CONFIG,
                CreateResourceIn(name=name),
                content=_body(b"x"),
            )


@pytest_asyncio.fixture(loop_scope="function")
async def manager(tmp_path: Path) -> PluginProcessManager:
    mgr = PluginProcessManager(tmp_path / "sockets")
    await mgr.start("huggingface", [sys.executable, str(FAKE_PLUGIN)])
    yield mgr
    await mgr.stop_all()


@pytest.mark.asyncio
async def test_huggingface_plugin_passes_the_shared_contract_suite(
    manager: PluginProcessManager,
) -> None:
    client = PluginClient(manager.socket_path("huggingface"))
    await run_contract_suite(client, config=CONFIG)

    with pytest.raises(PluginRPCError) as excinfo:
        await client.connect({"bucket": "me/other", "token": "t"})
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_large_upload_spills_to_a_temp_file_and_cleans_up(
    backend: HuggingFaceBackend,
    fake: FakeBucketApi,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import tempfile

    import plugins.huggingface.backend as hf

    monkeypatch.setattr(hf, "UPLOAD_SPOOL_BYTES", 4)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    async def chunks():  # noqa: ANN202, RUF029 -- async generator
        for part in (b"abc", b"def", b"ghi"):
            yield part

    await backend.create_resource(
        CONFIG, CreateResourceIn(name="big.bin"), content=chunks()
    )

    assert fake.objects["big.bin"] == b"abcdefghi"
    assert not list(tmp_path.glob("umedia-hf-*"))
