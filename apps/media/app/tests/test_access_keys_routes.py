from collections.abc import AsyncGenerator

import httpx
import pytest
import pytest_asyncio

ADMIN_CREDENTIALS = {
    "email": "access-admin@example.com",
    "password": "a secure admin password",
}
MEMBER_CREDENTIALS = {
    "email": "access-member@example.com",
    "password": "a secure member password",
}


def _anonymous(client: httpx.AsyncClient) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=client._transport,
        base_url=str(client.base_url),
    )


@pytest_asyncio.fixture(scope="module")
async def admin(client: httpx.AsyncClient) -> dict:
    response = await client.post("/auth/setup", json=ADMIN_CREDENTIALS)
    assert response.status_code == 201, response.text
    return response.json()["user"]


@pytest_asyncio.fixture(scope="module")
async def member(
    client: httpx.AsyncClient,
    admin: dict,
) -> dict:
    response = await client.post(
        "/users",
        json={**MEMBER_CREDENTIALS, "role": "user"},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest_asyncio.fixture
async def member_client(
    client: httpx.AsyncClient,
    member: dict,
) -> AsyncGenerator[httpx.AsyncClient]:
    async with _anonymous(client) as logged_in:
        response = await logged_in.post(
            "/auth/sessions",
            json=MEMBER_CREDENTIALS,
        )
        assert response.status_code == 201, response.text
        yield logged_in


@pytest.mark.asyncio
async def test_list_returns_only_the_current_users_keys_without_secrets(
    client: httpx.AsyncClient,
    admin: dict,
    member_client: httpx.AsyncClient,
) -> None:
    response = await client.get("/access-keys")

    assert response.status_code == 200, response.text
    keys = response.json()
    assert keys
    assert all(key["label"] == "default" for key in keys)
    assert all("secret_access_key" not in key for key in keys)
    assert keys != (await member_client.get("/access-keys")).json()


@pytest.mark.asyncio
async def test_create_returns_the_secret_once_and_defaults_label_to_key(
    client: httpx.AsyncClient,
    admin: dict,
) -> None:
    created = await client.post("/access-keys")

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["label"] == "key"
    assert body["secret_access_key"]

    listed = await client.get("/access-keys")
    matching = next(key for key in listed.json() if key["uid"] == body["uid"])
    assert "secret_access_key" not in matching


@pytest.mark.asyncio
async def test_delete_deactivates_an_owned_key(
    client: httpx.AsyncClient,
    admin: dict,
) -> None:
    created = await client.post("/access-keys", json={"label": "revoke me"})
    uid = created.json()["uid"]

    response = await client.delete(f"/access-keys/{uid}")

    assert response.status_code == 204, response.text
    listed = await client.get("/access-keys")
    revoked = next(key for key in listed.json() if key["uid"] == uid)
    assert revoked["is_active"] is False


@pytest.mark.asyncio
async def test_delete_returns_404_for_another_users_key(
    client: httpx.AsyncClient,
    admin: dict,
    member_client: httpx.AsyncClient,
) -> None:
    member_keys = (await member_client.get("/access-keys")).json()

    response = await client.delete(f"/access-keys/{member_keys[0]['uid']}")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_s3_connection_info_uses_the_public_request_origin(
    client: httpx.AsyncClient,
    admin: dict,
) -> None:
    response = await client.get(
        "/access-keys/s3",
        headers={
            "X-Forwarded-Host": "media.example.test",
            "X-Forwarded-Proto": "https",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "endpoint": "https://media.example.test/api/v1/s3",
        "region": "us-east-1",
        "bucket": "umedia",
        "force_path_style": True,
    }
