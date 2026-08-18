"""End-to-end smoke test over the real ASGI app: auth + provider routes.

Deliberately one test function, not several -- these steps share one
`httpx.AsyncClient` (and its cookie jar) and are only meaningful in this
order; splitting them into independently-orderable test functions would
just hide that same coupling instead of removing it.
"""

import httpx
import pytest


@pytest.mark.asyncio
async def test_bootstrap_login_and_browse_providers(
    client: httpx.AsyncClient,
) -> None:
    assert (await client.get("/auth/state")).json() == {
        "configured": False,
        "authenticated": False,
        "user": None,
    }
    assert (await client.get("/provider-types")).status_code == 401

    setup_response = await client.post(
        "/auth/setup",
        json={"email": "admin@example.com", "password": "a secure first password"},
    )
    assert setup_response.status_code == 201
    assert setup_response.json()["authenticated"] is True

    state = (await client.get("/auth/state")).json()
    assert state["configured"] is True
    assert state["authenticated"] is True
    assert state["user"]["email"] == "admin@example.com"
    assert state["user"]["roles"] == ["admin"]

    session_response = await client.get("/auth/sessions/current")
    assert session_response.status_code == 200
    current = session_response.json()
    assert current["email"] == "admin@example.com"
    assert current["roles"] == ["admin"]
    assert current["uid"] == state["user"]["uid"]

    types_response = await client.get("/provider-types")
    assert types_response.status_code == 200
    provider_ids = {item["id"] for item in types_response.json()}
    assert {"local", "s3", "telegram", "google_drive"} <= provider_ids

    connections_response = await client.get("/providers")
    assert connections_response.status_code == 200
    assert connections_response.json() == []

    rejected = await client.post(
        "/providers",
        json={"provider_type": "not-a-real-provider", "name": "x", "config": {}},
    )
    assert rejected.status_code == 422

    # A second /auth/setup is refused -- there is exactly one administrator.
    second_setup = await client.post(
        "/auth/setup",
        json={"email": "other@example.com", "password": "another secure password"},
    )
    assert second_setup.status_code == 409

    assert (await client.delete("/auth/sessions/current")).status_code == 204
    assert (await client.get("/provider-types")).status_code == 401

    login_response = await client.post(
        "/auth/sessions",
        json={"email": "admin@example.com", "password": "a secure first password"},
    )
    assert login_response.status_code == 201
    assert login_response.json()["authenticated"] is True
