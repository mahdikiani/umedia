from pathlib import Path

import pytest
import pytest_asyncio

from apps.media_files.errors import MediaFileNotFoundError, MediaFilePermissionError
from tests.media_file_helpers import FakeConnection, Harness, build_harness

CONNECTION_ID = "connection-1"
OWNER_ID = "user-1"
OTHER_USER_ID = "user-2"


@pytest_asyncio.fixture
async def harness(tmp_path: Path) -> Harness:
    built = await build_harness(tmp_path, FakeConnection(uid=CONNECTION_ID))
    yield built
    await built.engine.dispose()


async def _private_file(harness: Harness) -> str:
    record = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="private.txt",
        content=b"private bytes",
        owner_id=OWNER_ID,
    )
    return record.uid


@pytest.mark.asyncio
async def test_owner_mints_sigv4_temporary_link(harness: Harness) -> None:
    uid = await _private_file(harness)

    link = await harness.service.create_temporary_link(
        uid,
        actor_user_id=OWNER_ID,
        expires_in=3600,
        base_url="https://umedia.example",
    )

    assert link.url.startswith("/api/v1/s3/umedia/private.txt?")
    assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in link.url
    assert "X-Amz-SignedHeaders=host" in link.url
    assert "X-Amz-Signature=" in link.url
    assert link.key_id.startswith("um_")


@pytest.mark.asyncio
async def test_temporary_link_minting_requires_manage(harness: Harness) -> None:
    uid = await _private_file(harness)
    await harness.service.set_user_permission(
        uid,
        actor_user_id=OWNER_ID,
        target_user_id=OTHER_USER_ID,
        permission=10,
    )

    with pytest.raises(MediaFilePermissionError):
        await harness.service.create_temporary_link(
            uid,
            actor_user_id=OTHER_USER_ID,
            expires_in=3600,
            base_url="https://umedia.example",
        )


@pytest.mark.asyncio
async def test_hmac_query_no_longer_grants_private_public_alias_access(
    harness: Harness,
) -> None:
    uid = await _private_file(harness)

    with pytest.raises(MediaFileNotFoundError):
        await harness.service.get_via_public_link(uid, actor_user_id=None)

    await harness.service.set_public_permission(uid, "read", actor_user_id=OWNER_ID)
    visible = await harness.service.get_via_public_link(uid, actor_user_id=None)
    assert visible.uid == uid
