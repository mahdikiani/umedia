import httpx
import pytest

from apps.files.statics import get_icon_from_mime_type


def test_mime_icons_point_at_local_statics_not_pixy() -> None:
    pdf = get_icon_from_mime_type("application/pdf")
    folder = get_icon_from_mime_type("inode/directory")
    unknown = get_icon_from_mime_type("application/x-not-a-real-type")
    assert pdf == "/api/v1/statics/text_color_pdf.svg"
    assert folder == "/api/v1/statics/folder-1485.svg"
    assert unknown == "/api/v1/statics/unknown_flat_ico.svg"
    for url in (pdf, folder, unknown):
        assert "pixy" not in url
        assert url.startswith("/api/v1/statics/")


@pytest.mark.asyncio
async def test_local_statics_are_served_without_auth(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/statics/folder-1485.svg")
    assert response.status_code == 200
    assert "svg" in response.headers["content-type"]
    assert b"<svg" in response.content.lower()
    assert b"pixy" not in response.content
