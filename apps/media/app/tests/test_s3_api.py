from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials

from apps.user_access_keys.factory import build_user_access_key_service_from_state
from server.config import Settings
from server.server import app as fastapi_app

ADMIN_CREDENTIALS = {
    "email": "admin@example.com",
    "password": "a secure first password",
}


@dataclass(frozen=True)
class _S3File:
    uid: str
    name: str
    content: bytes
    connection_uid: str
    access_key: str
    secret_key: str
    key_uid: str


async def _authenticated(client: httpx.AsyncClient) -> str:
    state = (await client.get("/auth/state")).json()
    if state["configured"]:
        response = await client.post("/auth/sessions", json=ADMIN_CREDENTIALS)
    else:
        response = await client.post("/auth/setup", json=ADMIN_CREDENTIALS)
    assert response.status_code == 201, response.text
    return response.json()["user"]["uid"]


def _signed_headers(
    *,
    method: str,
    path: str,
    access_key: str,
    secret_key: str,
    body: bytes = b"",
    host: str | None = None,
) -> dict[str, str]:
    host = host or Settings.root_url
    request = AWSRequest(
        method=method,
        url=f"https://{host}{path}",
        data=body,
        headers={"Host": host, "x-amz-content-sha256": "UNSIGNED-PAYLOAD"},
    )
    SigV4Auth(
        Credentials(access_key, secret_key),
        "s3",
        Settings.S3_COMPAT_REGION,
    ).add_auth(request)
    return {**dict(request.headers), "Host": host}


@pytest.fixture(scope="module")
async def s3_file(client: httpx.AsyncClient) -> _S3File:
    user_id = await _authenticated(client)
    storage_root = Path(Settings().data_dir) / "storage" / "s3-api-library"
    connection = await client.post(
        "/providers",
        json={
            "provider_type": "local",
            "name": "s3-api-test-library",
            "config": {"root_path": str(storage_root)},
        },
    )
    assert connection.status_code == 201, connection.text
    content = b"content streamed through the MediaFile layer"
    uploaded = await client.post(
        "/files",
        data={
            "provider_connection_id": connection.json()["uid"],
            "name": "s3-shared.txt",
        },
        files={"file": ("s3-shared.txt", content, "text/plain")},
    )
    assert uploaded.status_code == 201, uploaded.text

    access_keys = build_user_access_key_service_from_state(fastapi_app.state)
    key = await access_keys.ensure_default_key(user_id)
    return _S3File(
        uid=uploaded.json()["uid"],
        name="s3-shared.txt",
        content=content,
        connection_uid=connection.json()["uid"],
        access_key=key.access_key_id,
        secret_key=access_keys.decrypt_secret_str(key),
        key_uid=key.uid,
    )


@pytest.mark.asyncio
async def test_list_buckets(client: httpx.AsyncClient, s3_file: _S3File) -> None:
    path = f"{Settings.base_path}/s3"
    response = await client.get(
        "/s3",
        headers=_signed_headers(
            method="GET",
            path=path,
            access_key=s3_file.access_key,
            secret_key=s3_file.secret_key,
        ),
    )

    assert response.status_code == 200, response.text
    assert f"<Name>{Settings.S3_COMPAT_BUCKET}</Name>" in response.text


@pytest.mark.asyncio
async def test_head_s3_endpoint_is_allowed(
    client: httpx.AsyncClient, s3_file: _S3File,
) -> None:
    """Cyberduck probes `HEAD /s3` before ListBuckets; 405 aborts the session."""
    path = f"{Settings.base_path}/s3"
    response = await client.head(
        "/s3",
        headers=_signed_headers(
            method="HEAD",
            path=path,
            access_key=s3_file.access_key,
            secret_key=s3_file.secret_key,
        ),
    )
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_virtual_host_list_objects_on_service_root(
    client: httpx.AsyncClient, s3_file: _S3File,
) -> None:
    """Amazon S3 clients list a bucket at `https://{bucket}.{endpoint}/s3`."""
    path = f"{Settings.base_path}/s3"
    vhost = f"{Settings.S3_COMPAT_BUCKET}.{Settings.root_url}"
    response = await client.get(
        "/s3",
        headers=_signed_headers(
            method="GET",
            path=path,
            access_key=s3_file.access_key,
            secret_key=s3_file.secret_key,
            host=vhost,
        ),
    )
    assert response.status_code == 200, response.text
    assert f"<Key>{s3_file.name}</Key>" in response.text
    assert "<ListAllMyBucketsResult" not in response.text


@pytest.mark.asyncio
async def test_virtual_host_head_and_get_object(
    client: httpx.AsyncClient, s3_file: _S3File,
) -> None:
    vhost = f"{Settings.S3_COMPAT_BUCKET}.{Settings.root_url}"
    object_path = f"/{s3_file.name}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fastapi_app),
        base_url=f"https://{vhost}",
        follow_redirects=False,
    ) as root:
        head_response = await root.head(
            object_path,
            headers=_signed_headers(
                method="HEAD",
                path=object_path,
                access_key=s3_file.access_key,
                secret_key=s3_file.secret_key,
                host=vhost,
            ),
        )
        get_response = await root.get(
            object_path,
            headers=_signed_headers(
                method="GET",
                path=object_path,
                access_key=s3_file.access_key,
                secret_key=s3_file.secret_key,
                host=vhost,
            ),
        )
    assert head_response.status_code == 200, head_response.text
    assert get_response.status_code == 200, get_response.text
    assert get_response.content == s3_file.content


@pytest.mark.asyncio
async def test_root_path_list_objects_query(
    client: httpx.AsyncClient, s3_file: _S3File,
) -> None:
    """Cyberduck lists `GET /?encoding-type=&delimiter=/` on the hostname."""
    path = "/?delimiter=%2F&encoding-type=url&max-keys=1000&prefix="
    headers = _signed_headers(
        method="GET",
        path=path,
        access_key=s3_file.access_key,
        secret_key=s3_file.secret_key,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fastapi_app),
        base_url=f"https://{Settings.root_url}",
        follow_redirects=False,
    ) as root:
        response = await root.get(path, headers=headers)
    assert response.status_code == 200, response.text
    assert "application/xml" in response.headers.get("content-type", "")
    assert "<ListBucketResult" in response.text
    assert f"<Key>{s3_file.name}</Key>" in response.text
    assert f"<Prefix>{s3_file.uid}/</Prefix>" not in response.text
    assert "<ListAllMyBucketsResult" not in response.text


@pytest.mark.asyncio
async def test_get_and_head_by_library_path(
    client: httpx.AsyncClient,
    s3_file: _S3File,
) -> None:
    path = (
        f"{Settings.base_path}/s3/{Settings.S3_COMPAT_BUCKET}/"
        f"{s3_file.name}"
    )
    relative = path.removeprefix(Settings.base_path)

    get_response = await client.get(
        relative,
        headers=_signed_headers(
            method="GET",
            path=path,
            access_key=s3_file.access_key,
            secret_key=s3_file.secret_key,
        ),
    )
    head_response = await client.head(
        relative,
        headers=_signed_headers(
            method="HEAD",
            path=path,
            access_key=s3_file.access_key,
            secret_key=s3_file.secret_key,
        ),
    )

    assert get_response.status_code == 200, get_response.text
    assert get_response.content == s3_file.content
    assert head_response.status_code == 200, head_response.text
    assert head_response.headers["content-length"] == str(len(s3_file.content))
    assert head_response.headers["content-type"] == "text/plain; charset=utf-8"


@pytest.mark.asyncio
async def test_get_legacy_path_without_bucket_still_works(
    client: httpx.AsyncClient,
    s3_file: _S3File,
) -> None:
    user_id = (await client.get("/auth/state")).json()["user"]["uid"]
    access_keys = build_user_access_key_service_from_state(fastapi_app.state)
    key_record = await access_keys.ensure_default_key(user_id)
    access_key = key_record.access_key_id
    secret_key = access_keys.decrypt_secret_str(key_record)
    canonical_path = (
        f"{Settings.base_path}/s3/{Settings.S3_COMPAT_BUCKET}/docs/note.txt"
    )
    content = b"nested legacy-path content"
    put_response = await client.put(
        canonical_path.removeprefix(Settings.base_path),
        content=content,
        headers=_signed_headers(
            method="PUT",
            path=canonical_path,
            access_key=access_key,
            secret_key=secret_key,
            body=content,
        ),
    )
    assert put_response.status_code == 200, put_response.text

    path = f"{Settings.base_path}/s3/docs/note.txt"
    response = await client.get(
        path.removeprefix(Settings.base_path),
        headers=_signed_headers(
            method="GET",
            path=path,
            access_key=access_key,
            secret_key=secret_key,
        ),
    )
    assert response.status_code == 200, response.text
    assert response.content == content


@pytest.mark.asyncio
async def test_list_objects_returns_library_path_keys(
    client: httpx.AsyncClient,
    s3_file: _S3File,
) -> None:
    query = "list-type=2"
    path = f"{Settings.base_path}/s3/{Settings.S3_COMPAT_BUCKET}?{query}"
    response = await client.get(
        path.removeprefix(Settings.base_path),
        headers=_signed_headers(
            method="GET",
            path=path,
            access_key=s3_file.access_key,
            secret_key=s3_file.secret_key,
        ),
    )

    assert response.status_code == 200, response.text
    assert f"<Key>{s3_file.name}</Key>" in response.text
    assert f"<Size>{len(s3_file.content)}</Size>" in response.text


@pytest.mark.asyncio
async def test_temporary_link_round_trip_and_key_revocation(
    client: httpx.AsyncClient,
    s3_file: _S3File,
) -> None:
    await _authenticated(client)
    minted = await client.post(
        f"/files/{s3_file.uid}/temporary-link",
        json={"expires_in": 3600},
    )
    assert minted.status_code == 200, minted.text
    body = minted.json()
    assert body["url"].startswith(
        f"{Settings.base_path}/s3/{Settings.S3_COMPAT_BUCKET}/"
        f"{s3_file.name}?",
    )
    assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in body["url"]
    assert "X-Amz-Signature=" in body["url"]

    anonymous = httpx.AsyncClient(
        transport=client._transport,
        base_url=str(client.base_url),
    )
    async with anonymous:
        shared = await anonymous.get(body["url"].removeprefix(Settings.base_path))
        assert shared.status_code == 200, shared.text
        assert shared.content == s3_file.content

        access_keys = build_user_access_key_service_from_state(fastapi_app.state)
        await access_keys.deactivate(s3_file.key_uid)

        revoked = await anonymous.get(body["url"].removeprefix(Settings.base_path))
        assert revoked.status_code == 403
        assert "<Code>InvalidAccessKeyId</Code>" in revoked.text


@pytest.mark.asyncio
async def test_put_object_then_get_by_client_key(
    client: httpx.AsyncClient,
    s3_file: _S3File,
) -> None:
    user_id = (await client.get("/auth/state")).json()["user"]["uid"]
    access_keys = build_user_access_key_service_from_state(fastapi_app.state)
    # A prior test may have revoked the module-scoped default key.
    key_record = await access_keys.ensure_default_key(user_id)
    access_key = key_record.access_key_id
    secret_key = access_keys.decrypt_secret_str(key_record)
    content = b"hello from s3 put\n"
    key = "s3-put-test.txt"
    path = f"{Settings.base_path}/s3/{Settings.S3_COMPAT_BUCKET}/{key}"
    put_response = await client.put(
        path.removeprefix(Settings.base_path),
        content=content,
        headers=_signed_headers(
            method="PUT",
            path=path,
            access_key=access_key,
            secret_key=secret_key,
            body=content,
        ),
    )
    assert put_response.status_code == 200, put_response.text
    assert put_response.headers.get("etag")

    get_response = await client.get(
        path.removeprefix(Settings.base_path),
        headers=_signed_headers(
            method="GET",
            path=path,
            access_key=access_key,
            secret_key=secret_key,
        ),
    )
    assert get_response.status_code == 200, get_response.text
    assert get_response.content == content

    head_response = await client.head(
        path.removeprefix(Settings.base_path),
        headers=_signed_headers(
            method="HEAD",
            path=path,
            access_key=access_key,
            secret_key=secret_key,
        ),
    )
    assert head_response.status_code == 200, head_response.text
    assert head_response.headers["content-length"] == str(len(content))


@pytest.mark.asyncio
async def test_put_object_with_persian_filename(
    client: httpx.AsyncClient,
    s3_file: _S3File,
) -> None:
    """Cyberduck PutObject of `سیدیوسف_درسته-fa.pdf` used to 400:
    httpx ASCII-encoded the plugin metadata header."""
    user_id = (await client.get("/auth/state")).json()["user"]["uid"]
    access_keys = build_user_access_key_service_from_state(fastapi_app.state)
    key_record = await access_keys.ensure_default_key(user_id)
    access_key = key_record.access_key_id
    secret_key = access_keys.decrypt_secret_str(key_record)
    key = "سیدیوسف_درسته-fa.pdf"
    content = b"%PDF-umedia-persian"
    path = f"{Settings.base_path}/s3/{Settings.S3_COMPAT_BUCKET}/{key}"
    put_response = await client.put(
        path.removeprefix(Settings.base_path),
        content=content,
        headers=_signed_headers(
            method="PUT",
            path=path,
            access_key=access_key,
            secret_key=secret_key,
            body=content,
        ),
    )
    assert put_response.status_code == 200, put_response.text
    get_response = await client.get(
        path.removeprefix(Settings.base_path),
        headers=_signed_headers(
            method="GET",
            path=path,
            access_key=access_key,
            secret_key=secret_key,
        ),
    )
    assert get_response.status_code == 200, get_response.text
    assert get_response.content == content


@pytest.mark.asyncio
async def test_delete_object_removes_key_from_library_listing(
    client: httpx.AsyncClient,
    s3_file: _S3File,
) -> None:
    """Cyberduck DeleteObject is a MediaFile soft-delete; S3 listing hides it."""
    user_id = (await client.get("/auth/state")).json()["user"]["uid"]
    access_keys = build_user_access_key_service_from_state(fastapi_app.state)
    key_record = await access_keys.ensure_default_key(user_id)
    access_key = key_record.access_key_id
    secret_key = access_keys.decrypt_secret_str(key_record)
    key = "s3-delete-me.txt"
    content = b"gone soon"
    path = f"{Settings.base_path}/s3/{Settings.S3_COMPAT_BUCKET}/{key}"
    put_response = await client.put(
        path.removeprefix(Settings.base_path),
        content=content,
        headers=_signed_headers(
            method="PUT",
            path=path,
            access_key=access_key,
            secret_key=secret_key,
            body=content,
        ),
    )
    assert put_response.status_code == 200, put_response.text

    delete_response = await client.delete(
        path.removeprefix(Settings.base_path),
        headers=_signed_headers(
            method="DELETE",
            path=path,
            access_key=access_key,
            secret_key=secret_key,
        ),
    )
    assert delete_response.status_code == 204, delete_response.text

    get_response = await client.get(
        path.removeprefix(Settings.base_path),
        headers=_signed_headers(
            method="GET",
            path=path,
            access_key=access_key,
            secret_key=secret_key,
        ),
    )
    assert get_response.status_code == 404
    assert "<Code>NoSuchKey</Code>" in get_response.text

    missing = await client.delete(
        path.removeprefix(Settings.base_path),
        headers=_signed_headers(
            method="DELETE",
            path=path,
            access_key=access_key,
            secret_key=secret_key,
        ),
    )
    assert missing.status_code == 204, missing.text

    listed = await client.get(
        f"/s3/{Settings.S3_COMPAT_BUCKET}?list-type=2",
        headers=_signed_headers(
            method="GET",
            path=f"{Settings.base_path}/s3/{Settings.S3_COMPAT_BUCKET}?list-type=2",
            access_key=access_key,
            secret_key=secret_key,
        ),
    )
    assert listed.status_code == 200, listed.text
    assert "<Key>s3-delete-me.txt</Key>" not in listed.text


@pytest.mark.asyncio
async def test_delete_objects_post_removes_keys(
    client: httpx.AsyncClient,
    s3_file: _S3File,
) -> None:
    """Cyberduck multi-delete is POST /{bucket}?delete with an XML body."""
    user_id = (await client.get("/auth/state")).json()["user"]["uid"]
    access_keys = build_user_access_key_service_from_state(fastapi_app.state)
    key_record = await access_keys.ensure_default_key(user_id)
    access_key = key_record.access_key_id
    secret_key = access_keys.decrypt_secret_str(key_record)
    key = "s3-multi-delete.txt"
    content = b"batch delete"
    object_path = f"{Settings.base_path}/s3/{Settings.S3_COMPAT_BUCKET}/{key}"
    put_response = await client.put(
        object_path.removeprefix(Settings.base_path),
        content=content,
        headers=_signed_headers(
            method="PUT",
            path=object_path,
            access_key=access_key,
            secret_key=secret_key,
            body=content,
        ),
    )
    assert put_response.status_code == 200, put_response.text

    body = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b"<Delete><Object><Key>s3-multi-delete.txt</Key></Object></Delete>"
    )
    delete_path = f"{Settings.base_path}/s3/{Settings.S3_COMPAT_BUCKET}?delete"
    response = await client.post(
        delete_path.removeprefix(Settings.base_path),
        content=body,
        headers=_signed_headers(
            method="POST",
            path=delete_path,
            access_key=access_key,
            secret_key=secret_key,
            body=body,
        ),
    )
    assert response.status_code == 200, response.text
    assert "<Deleted>" in response.text
    assert "s3-multi-delete.txt" in response.text

    get_response = await client.get(
        object_path.removeprefix(Settings.base_path),
        headers=_signed_headers(
            method="GET",
            path=object_path,
            access_key=access_key,
            secret_key=secret_key,
        ),
    )
    assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_put_nested_key_creates_library_folders_and_lists_prefix(
    client: httpx.AsyncClient,
    s3_file: _S3File,
) -> None:
    user_id = (await client.get("/auth/state")).json()["user"]["uid"]
    access_keys = build_user_access_key_service_from_state(fastapi_app.state)
    key_record = await access_keys.ensure_default_key(user_id)
    access_key = key_record.access_key_id
    secret_key = access_keys.decrypt_secret_str(key_record)
    key = "trip/f72.txt"
    content = b"nested s3 body"
    path = f"{Settings.base_path}/s3/{Settings.S3_COMPAT_BUCKET}/{key}"

    response = await client.put(
        path.removeprefix(Settings.base_path),
        content=content,
        headers=_signed_headers(
            method="PUT",
            path=path,
            access_key=access_key,
            secret_key=secret_key,
            body=content,
        ),
    )

    assert response.status_code == 200, response.text
    query = "list-type=2&delimiter=%2F"
    list_path = f"{Settings.base_path}/s3/{Settings.S3_COMPAT_BUCKET}?{query}"
    listed = await client.get(
        list_path.removeprefix(Settings.base_path),
        headers=_signed_headers(
            method="GET",
            path=list_path,
            access_key=access_key,
            secret_key=secret_key,
        ),
    )
    assert "<Prefix>trip/</Prefix>" in listed.text
    fetched = await client.get(
        path.removeprefix(Settings.base_path),
        headers=_signed_headers(
            method="GET",
            path=path,
            access_key=access_key,
            secret_key=secret_key,
        ),
    )
    assert fetched.content == content
    root = (await client.get("/files?limit=200")).json()["items"]
    folder = next(item for item in root if item["name"] == "trip")
    children = (
        await client.get(f"/files?parent_id={folder['uid']}&limit=200")
    ).json()["items"]
    assert [item["name"] for item in children] == ["f72.txt"]


@pytest.mark.asyncio
async def test_duplicate_library_names_have_distinct_projected_keys(
    client: httpx.AsyncClient,
    s3_file: _S3File,
) -> None:
    second_root = Path(Settings().data_dir) / "storage" / "s3-api-duplicates"
    second_connection = await client.post(
        "/providers",
        json={
            "provider_type": "local",
            "name": "s3-duplicate-test-library",
            "config": {"root_path": str(second_root)},
        },
    )
    assert second_connection.status_code == 201, second_connection.text
    first = await client.post(
        "/files",
        data={
            "provider_connection_id": s3_file.connection_uid,
            "name": "dup.txt",
        },
        files={"file": ("dup.txt", b"first duplicate", "text/plain")},
    )
    second = await client.post(
        "/files",
        data={
            "provider_connection_id": second_connection.json()["uid"],
            "name": "dup.txt",
        },
        files={"file": ("dup.txt", b"second duplicate", "text/plain")},
    )
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text

    user_id = (await client.get("/auth/state")).json()["user"]["uid"]
    access_keys = build_user_access_key_service_from_state(fastapi_app.state)
    key_record = await access_keys.ensure_default_key(user_id)
    access_key = key_record.access_key_id
    secret_key = access_keys.decrypt_secret_str(key_record)
    list_path = f"{Settings.base_path}/s3/{Settings.S3_COMPAT_BUCKET}?list-type=2"
    listed = await client.get(
        list_path.removeprefix(Settings.base_path),
        headers=_signed_headers(
            method="GET",
            path=list_path,
            access_key=access_key,
            secret_key=secret_key,
        ),
    )
    assert "<Key>dup.txt</Key>" in listed.text
    assert "<Key>dup%20(2).txt</Key>" in listed.text
    for key, expected in (
        ("dup.txt", b"first duplicate"),
        ("dup (2).txt", b"second duplicate"),
    ):
        path = f"{Settings.base_path}/s3/{Settings.S3_COMPAT_BUCKET}/{key}"
        response = await client.get(
            path.removeprefix(Settings.base_path),
            headers=_signed_headers(
                method="GET",
                path=path,
                access_key=access_key,
                secret_key=secret_key,
            ),
        )
        assert response.content == expected


@pytest.mark.asyncio
async def test_put_same_projected_key_overwrites_existing_media_file(
    client: httpx.AsyncClient,
    s3_file: _S3File,
) -> None:
    user_id = (await client.get("/auth/state")).json()["user"]["uid"]
    access_keys = build_user_access_key_service_from_state(fastapi_app.state)
    key_record = await access_keys.ensure_default_key(user_id)
    access_key = key_record.access_key_id
    secret_key = access_keys.decrypt_secret_str(key_record)
    key = "s3-overwrite.txt"
    path = f"{Settings.base_path}/s3/{Settings.S3_COMPAT_BUCKET}/{key}"
    for content in (b"old body", b"new body"):
        response = await client.put(
            path.removeprefix(Settings.base_path),
            content=content,
            headers=_signed_headers(
                method="PUT",
                path=path,
                access_key=access_key,
                secret_key=secret_key,
                body=content,
            ),
        )
        assert response.status_code == 200, response.text

    fetched = await client.get(
        path.removeprefix(Settings.base_path),
        headers=_signed_headers(
            method="GET",
            path=path,
            access_key=access_key,
            secret_key=secret_key,
        ),
    )
    assert fetched.content == b"new body"
    list_path = f"{Settings.base_path}/s3/{Settings.S3_COMPAT_BUCKET}?list-type=2"
    listed = await client.get(
        list_path.removeprefix(Settings.base_path),
        headers=_signed_headers(
            method="GET",
            path=list_path,
            access_key=access_key,
            secret_key=secret_key,
        ),
    )
    assert listed.text.count("<Key>s3-overwrite.txt</Key>") == 1


@pytest.mark.asyncio
async def test_put_object_accepts_macos_narrow_nbsp_filename(
    client: httpx.AsyncClient,
    s3_file: _S3File,
) -> None:
    """Cyberduck uploads `Screenshot … 2.04.22 PM.png` with U+202F before PM."""
    user_id = (await client.get("/auth/state")).json()["user"]["uid"]
    access_keys = build_user_access_key_service_from_state(fastapi_app.state)
    key_record = await access_keys.ensure_default_key(user_id)
    access_key = key_record.access_key_id
    secret_key = access_keys.decrypt_secret_str(key_record)
    filename = "Screenshot 1405-05-26 at 2.04.22\u202fPM.png"
    content = b"png-bytes"
    path = f"{Settings.base_path}/s3/{Settings.S3_COMPAT_BUCKET}/{filename}"
    put_response = await client.put(
        path.removeprefix(Settings.base_path),
        content=content,
        headers=_signed_headers(
            method="PUT",
            path=path,
            access_key=access_key,
            secret_key=secret_key,
            body=content,
        ),
    )
    assert put_response.status_code == 200, put_response.text
    get_response = await client.get(
        path.removeprefix(Settings.base_path),
        headers=_signed_headers(
            method="GET",
            path=path,
            access_key=access_key,
            secret_key=secret_key,
        ),
    )
    assert get_response.status_code == 200, get_response.text
    assert get_response.content == content
