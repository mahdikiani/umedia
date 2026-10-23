"""Provider-reported hashes are indexed from listings; downloads are the
fallback. Also covers the hash progress counts and the rclone/migration glue."""

import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from apps.media_files.hashing import pick_native_hash
from plugins.contracts import Resource as PluginResource
from plugins.rclone.backend import NATIVE_HASH_REMOTES, RcloneBackend
from tests.media_file_helpers import FakeConnection, Harness, build_harness

CONNECTION_ID = "connection-1"
ACTOR_ID = "admin-1"
MD5 = "900150983cd24fb0d6963f7d28e17f72"


def _listing(*files: PluginResource) -> dict[str | None, list[PluginResource]]:
    return {None: list(files)}


def _file(name: str, *, size: int = 3, mtime: float = 1.0, **kwargs: object):
    return PluginResource(
        id=name,
        type="file",
        name=name,
        size=size,
        metadata={"mtime": mtime},
        **kwargs,
    )


@pytest_asyncio.fixture
async def harness(tmp_path: Path) -> Harness:
    built = await build_harness(
        tmp_path,
        FakeConnection(uid=CONNECTION_ID, name="Lib", import_existing=True),
    )
    yield built
    await built.engine.dispose()


async def _import(harness: Harness) -> None:
    await harness.service.import_from_provider(CONNECTION_ID, actor_user_id=ACTOR_ID)


async def _object(harness: Harness, reference: str):
    objects = await harness.objects.list(provider_connection_id=CONNECTION_ID)
    return next(o for o in objects if o.content_reference == reference)


def test_pick_native_hash_prefers_strongest_and_normalizes() -> None:
    assert pick_native_hash({"MD5": " ABC ", "sha1": "def"}) == ("sha1", "def")
    assert pick_native_hash({"md5": "abc", "sha256": "123"}) == ("sha256", "123")
    assert pick_native_hash({"quickxor": "q", "dropbox": "d"}) == ("dropbox", "d")
    assert pick_native_hash({"md5": "  ", "sha1": ""}) is None
    assert pick_native_hash(None) is None


def test_listing_hashes_default_to_empty() -> None:
    assert PluginResource(id="a", type="file", name="a").hashes == {}


@pytest.mark.asyncio
async def test_listing_hash_is_indexed_without_any_download(harness: Harness) -> None:
    harness.plugins.listings[CONNECTION_ID] = _listing(
        _file("a.txt", hashes={"md5": MD5}),
    )

    await _import(harness)

    obj = await _object(harness, "a.txt")
    assert (obj.content_hash, obj.hash_algorithm) == (MD5, "md5")
    assert await harness.service.hash_missing_content(CONNECTION_ID) == 0
    assert harness.plugins.plugin_calls("read") == []


@pytest.mark.asyncio
async def test_files_without_provider_hash_fall_back_to_sha256_backfill(
    harness: Harness,
) -> None:
    harness.plugins.listings[CONNECTION_ID] = _listing(
        _file("native.txt", hashes={"md5": MD5}),
        _file("plain.txt"),
    )
    harness.plugins.store["plain.txt"] = b"abc"
    await _import(harness)

    assert await harness.service.hash_missing_content(CONNECTION_ID) == 1

    plain = await _object(harness, "plain.txt")
    assert plain.hash_algorithm == "sha256"
    assert plain.content_hash == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
    assert [c[2] for c in harness.plugins.plugin_calls("read")] == ["plain.txt"]


@pytest.mark.asyncio
async def test_changed_provider_hash_replaces_the_stored_one(harness: Harness) -> None:
    harness.plugins.listings[CONNECTION_ID] = _listing(
        _file("a.txt", hashes={"md5": MD5}),
    )
    await _import(harness)

    new_md5 = "0cc175b9c0f1b6a831c399e269772661"
    harness.plugins.listings[CONNECTION_ID] = _listing(
        _file("a.txt", hashes={"md5": new_md5}),  # same size and mtime
    )
    await _import(harness)

    obj = await _object(harness, "a.txt")
    assert (obj.content_hash, obj.hash_algorithm) == (new_md5, "md5")


@pytest.mark.asyncio
async def test_unchanged_file_keeps_existing_sha256_over_a_different_algorithm(
    harness: Harness,
) -> None:
    harness.plugins.listings[CONNECTION_ID] = _listing(_file("a.txt"))
    harness.plugins.store["a.txt"] = b"abc"
    await _import(harness)
    await harness.service.hash_missing_content(CONNECTION_ID)
    sha = (await _object(harness, "a.txt")).content_hash

    harness.plugins.listings[CONNECTION_ID] = _listing(
        _file("a.txt", hashes={"md5": MD5}),
    )
    await _import(harness)

    obj = await _object(harness, "a.txt")
    assert (obj.content_hash, obj.hash_algorithm) == (sha, "sha256")


@pytest.mark.asyncio
async def test_hash_progress_counts_native_computed_and_pending(
    harness: Harness,
) -> None:
    harness.plugins.listings[CONNECTION_ID] = _listing(
        _file("n.txt", hashes={"md5": MD5}),
        _file("p1.txt"),
        _file("p2.txt"),
    )
    harness.plugins.store.update({"p1.txt": b"a", "p2.txt": b"b"})
    await _import(harness)

    before = await harness.objects.hash_progress(provider_connection_id=CONNECTION_ID)
    assert before == {"computed": 0, "native": 1, "pending": 2}

    await harness.service.hash_missing_content(CONNECTION_ID)

    after = await harness.objects.hash_progress(provider_connection_id=CONNECTION_ID)
    assert after == {"computed": 2, "native": 1, "pending": 0}


def test_rclone_resource_carries_listing_hashes() -> None:
    backend = RcloneBackend()
    resource = backend._to_resource({
        "Path": "docs/a.txt",
        "Name": "a.txt",
        "Size": 3,
        "IsDir": False,
        "Hashes": {"MD5": MD5, "sha1": ""},
    })
    assert resource.hashes == {"md5": MD5}
    folder = backend._to_resource({"Path": "docs", "Name": "docs", "IsDir": True})
    assert folder.hashes == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("remote_type", "expects_hashes"),
    [("s3", True), ("google_drive", True), ("sftp", False), ("ftp", False)],
)
async def test_rclone_requests_hashes_only_where_they_are_free(
    remote_type: str, expects_hashes: bool,
) -> None:
    backend = RcloneBackend()
    rc_call = AsyncMock(return_value={"list": []})
    with (
        patch("plugins.rclone.backend._build_fs", return_value="remote:"),
        patch.object(backend, "_rc_call", rc_call),
    ):
        await backend.list_resources({"remote_type": remote_type}, parent_id=None)

    params = rc_call.await_args.args[1]
    assert ("opt" in params) is expects_hashes
    assert (remote_type in NATIVE_HASH_REMOTES) is expects_hashes


def test_migration_0017_follows_0016_and_backfills_sha256() -> None:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0017_storage_object_hash_algorithm.py"
    )
    spec = importlib.util.spec_from_file_location("hash_algorithm_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.down_revision == "0016_media_file_object_join_index"
