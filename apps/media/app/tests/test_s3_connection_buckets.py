"""Each connection is its own S3 bucket, named after the connection.

`umedia` still projects the whole library; a connection bucket projects
only the files stored on that connection, under the same library-path
keys. PutObject into a connection bucket stores the file on it.
"""

from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from apps.user_access_keys.factory import build_user_access_key_service_from_state
from server.config import Settings
from server.server import app as fastapi_app

from .test_s3_api import _authenticated, _signed_headers

BUCKET_A = "bucket-test-alpha"
BUCKET_B = "bucket-test-beta"


@dataclass(frozen=True)
class _Setup:
    uid_a: str
    uid_b: str
    access_key: str
    secret_key: str


async def _connection(client: httpx.AsyncClient, name: str) -> str:
    root = Path(Settings().data_dir) / "storage" / name
    response = await client.post(
        "/providers",
        json={
            "provider_type": "local",
            "name": name,
            "config": {"root_path": str(root)},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["uid"]


async def _upload(client: httpx.AsyncClient, connection_uid: str, name: str) -> None:
    response = await client.post(
        "/files",
        data={"provider_connection_id": connection_uid, "name": name},
        files={"file": (name, name.encode(), "text/plain")},
    )
    assert response.status_code == 201, response.text


@pytest.fixture(scope="module")
async def setup(client: httpx.AsyncClient) -> _Setup:
    user_id = await _authenticated(client)
    uid_a = await _connection(client, BUCKET_A)
    uid_b = await _connection(client, BUCKET_B)
    await _upload(client, uid_a, "only-on-alpha.txt")
    await _upload(client, uid_b, "only-on-beta.txt")
    access_keys = build_user_access_key_service_from_state(fastapi_app.state)
    key = await access_keys.ensure_default_key(user_id)
    return _Setup(
        uid_a=uid_a,
        uid_b=uid_b,
        access_key=key.access_key_id,
        secret_key=access_keys.decrypt_secret_str(key),
    )


async def _s3(
    client: httpx.AsyncClient,
    setup: _Setup,
    method: str,
    path: str,
    body: bytes = b"",
) -> httpx.Response:
    """Signed request to `/api/v1/s3{path}` (path may carry a query)."""
    full = f"{Settings.base_path}/s3{path}"
    return await client.request(
        method,
        f"/s3{path}",
        content=body or None,
        headers=_signed_headers(
            method=method,
            path=full,
            access_key=setup.access_key,
            secret_key=setup.secret_key,
            body=body,
        ),
    )


@pytest.mark.asyncio
async def test_list_buckets_has_the_library_and_one_bucket_per_connection(
    client: httpx.AsyncClient,
    setup: _Setup,
) -> None:
    response = await _s3(client, setup, "GET", "")

    assert response.status_code == 200, response.text
    for bucket in (Settings.S3_COMPAT_BUCKET, BUCKET_A, BUCKET_B):
        assert f"<Name>{bucket}</Name>" in response.text


@pytest.mark.asyncio
async def test_connection_bucket_lists_only_its_own_files(
    client: httpx.AsyncClient,
    setup: _Setup,
) -> None:
    alpha = await _s3(client, setup, "GET", f"/{BUCKET_A}?list-type=2")
    library = await _s3(
        client,
        setup,
        "GET",
        f"/{Settings.S3_COMPAT_BUCKET}?list-type=2",
    )

    assert alpha.status_code == 200, alpha.text
    assert "<Key>only-on-alpha.txt</Key>" in alpha.text
    assert "only-on-beta.txt" not in alpha.text
    assert f"<Name>{BUCKET_A}</Name>" in alpha.text
    # Same key in the library bucket.
    assert "<Key>only-on-alpha.txt</Key>" in library.text
    assert "<Key>only-on-beta.txt</Key>" in library.text


@pytest.mark.asyncio
async def test_get_is_scoped_to_the_bucket(
    client: httpx.AsyncClient,
    setup: _Setup,
) -> None:
    own = await _s3(client, setup, "GET", f"/{BUCKET_A}/only-on-alpha.txt")
    other = await _s3(client, setup, "GET", f"/{BUCKET_A}/only-on-beta.txt")
    head = await _s3(client, setup, "HEAD", f"/{BUCKET_B}/only-on-beta.txt")

    assert own.status_code == 200, own.text
    assert own.content == b"only-on-alpha.txt"
    assert other.status_code == 404
    assert "<Code>NoSuchKey</Code>" in other.text
    assert head.status_code == 200


@pytest.mark.asyncio
async def test_put_into_a_connection_bucket_stores_on_that_connection(
    client: httpx.AsyncClient,
    setup: _Setup,
) -> None:
    put = await _s3(
        client,
        setup,
        "PUT",
        f"/{BUCKET_B}/uploaded-to-beta.txt",
        b"beta bytes",
    )
    assert put.status_code == 200, put.text

    beta = await _s3(client, setup, "GET", f"/{BUCKET_B}?list-type=2")
    alpha = await _s3(client, setup, "GET", f"/{BUCKET_A}?list-type=2")
    assert "<Key>uploaded-to-beta.txt</Key>" in beta.text
    assert "uploaded-to-beta.txt" not in alpha.text

    stored = Path(Settings().data_dir) / "storage" / BUCKET_B
    assert any(path.read_bytes() == b"beta bytes" for path in stored.rglob("*.txt"))


@pytest.mark.asyncio
async def test_put_refuses_a_key_that_lives_on_another_connection(
    client: httpx.AsyncClient,
    setup: _Setup,
) -> None:
    put = await _s3(client, setup, "PUT", f"/{BUCKET_A}/only-on-beta.txt", b"x")

    assert put.status_code == 400
    assert "another connection" in put.text
    original = await _s3(client, setup, "GET", f"/{BUCKET_B}/only-on-beta.txt")
    assert original.content == b"only-on-beta.txt"


@pytest.mark.asyncio
async def test_delete_through_a_bucket_never_touches_other_connections(
    client: httpx.AsyncClient,
    setup: _Setup,
) -> None:
    deleted = await _s3(client, setup, "DELETE", f"/{BUCKET_A}/only-on-beta.txt")

    assert deleted.status_code == 204
    still_there = await _s3(client, setup, "GET", f"/{BUCKET_B}/only-on-beta.txt")
    assert still_there.status_code == 200


@pytest.mark.asyncio
async def test_unknown_bucket_is_no_such_bucket(
    client: httpx.AsyncClient,
    setup: _Setup,
) -> None:
    response = await _s3(client, setup, "GET", "/no-such-connection?list-type=2")
    head = await _s3(client, setup, "HEAD", "/no-such-connection")

    assert response.status_code == 404
    assert "<Code>NoSuchBucket</Code>" in response.text
    assert head.status_code == 404


@pytest.mark.asyncio
async def test_virtual_host_addresses_a_connection_bucket(
    client: httpx.AsyncClient,
    setup: _Setup,
) -> None:
    vhost = f"{BUCKET_A}.{Settings.root_url}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fastapi_app),
        base_url=f"https://{vhost}",
    ) as root:
        listing = await root.get(
            "/",
            headers=_signed_headers(
                method="GET",
                path="/",
                access_key=setup.access_key,
                secret_key=setup.secret_key,
                host=vhost,
            ),
        )
        obj = await root.get(
            "/only-on-alpha.txt",
            headers=_signed_headers(
                method="GET",
                path="/only-on-alpha.txt",
                access_key=setup.access_key,
                secret_key=setup.secret_key,
                host=vhost,
            ),
        )

    assert listing.status_code == 200, listing.text
    assert "<Key>only-on-alpha.txt</Key>" in listing.text
    assert "only-on-beta.txt" not in listing.text
    assert obj.content == b"only-on-alpha.txt"


@pytest.mark.asyncio
async def test_renaming_a_connection_renames_its_bucket(
    client: httpx.AsyncClient,
    setup: _Setup,
) -> None:
    renamed = "bucket-test-gamma"
    response = await client.patch(f"/providers/{setup.uid_a}", json={"name": renamed})
    assert response.status_code == 200, response.text
    try:
        old = await _s3(client, setup, "GET", f"/{BUCKET_A}?list-type=2")
        new = await _s3(client, setup, "GET", f"/{renamed}?list-type=2")
        assert old.status_code == 404
        assert "<Key>only-on-alpha.txt</Key>" in new.text
    finally:
        await client.patch(f"/providers/{setup.uid_a}", json={"name": BUCKET_A})


@pytest.mark.asyncio
async def test_disabled_connection_is_not_a_bucket(
    client: httpx.AsyncClient,
    setup: _Setup,
) -> None:
    await client.patch(f"/providers/{setup.uid_b}", json={"enabled": False})
    try:
        buckets = await _s3(client, setup, "GET", "")
        listing = await _s3(client, setup, "GET", f"/{BUCKET_B}?list-type=2")
        assert f"<Name>{BUCKET_B}</Name>" not in buckets.text
        assert listing.status_code == 404
    finally:
        await client.patch(f"/providers/{setup.uid_b}", json={"enabled": True})
