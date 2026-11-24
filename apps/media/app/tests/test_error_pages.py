"""Owner's rule (2026-11-16): no user sees error text, a stack trace or a
developer page. An unexpected server error answers a browser with a plain
HTML page and an API client with JSON that carries no error text.
"""

import httpx
import pytest
from fastapi import FastAPI

from server.error_pages import install_error_pages
from server.server import app as real_app

SECRET_TEXT = "TypeError: boom at /srv/app/handler.js:12 secret-detail"
LEAKS = ["TypeError", "Error:", " at ", ".js", "secret-detail", "Traceback"]


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError(SECRET_TEXT)

    install_error_pages(app)
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    # The server error middleware re-raises after answering so error
    # tracking still sees the exception; the client must not re-raise it.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _assert_no_leak(body: str) -> None:
    for leak in LEAKS:
        assert leak not in body, leak


@pytest.mark.asyncio
async def test_browser_gets_plain_html_page() -> None:
    async with _client(_app()) as client:
        response = await client.get(
            "/boom",
            headers={"accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
        )
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("text/html")
    assert "Something went wrong." in response.text
    assert "Reload" in response.text
    assert 'href="/"' in response.text
    assert "mailto:" in response.text
    _assert_no_leak(response.text)


@pytest.mark.asyncio
async def test_api_client_gets_json_without_error_text() -> None:
    async with _client(_app()) as client:
        response = await client.get(
            "/boom",
            headers={"accept": "application/json"},
        )
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert body["message"] == "Internal server error"
    _assert_no_leak(response.text)


@pytest.mark.asyncio
async def test_api_client_without_accept_header_gets_json() -> None:
    async with _client(_app()) as client:
        response = await client.get("/boom", headers={"accept": "*/*"})
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    _assert_no_leak(response.text)


def test_real_app_uses_the_safe_handler() -> None:
    from server.error_pages import safe_exception_handler

    assert real_app.exception_handlers[Exception] is safe_exception_handler
