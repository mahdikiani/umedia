"""WebDAV gateway: every verb against the real ASGI app (httpx)."""

import base64
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree  # noqa: S405

import httpx
import pytest
import pytest_asyncio

from apps.user_access_keys.factory import build_user_access_key_service_from_state
from apps.webdav.locks import LockTable, submitted_tokens
from apps.webdav.paths import (
    BadPathError,
    clean_segment,
    destination_path,
    href,
    mount_relative,
)
from apps.webdav.service import DavError, byte_range
from server.config import Settings
from server.server import app as fastapi_app
from tests.test_s3_api import _authenticated

NS = {"D": "DAV:"}
PREFIX = Settings.WEBDAV_PREFIX


@dataclass(frozen=True)
class Dav:
    http: httpx.AsyncClient
    auth: dict[str, str]
    key_id: str
    secret: str

    async def call(
        self, method: str, path: str, **kwargs: object,
    ) -> httpx.Response:
        headers = {**self.auth, **kwargs.pop("headers", {})}  # type: ignore[arg-type]
        return await self.http.request(
            method, f"{PREFIX}{path}", headers=headers, **kwargs,
        )

    def destination(self, path: str) -> str:
        return f"https://{Settings.root_url}{PREFIX}{path}"


def _basic(user: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest_asyncio.fixture(scope="module")
async def dav(client: httpx.AsyncClient) -> Dav:
    user_id = await _authenticated(client)
    storage_root = Path(Settings().data_dir) / "storage" / "webdav-library"
    connection = await client.post(
        "/providers",
        json={
            "provider_type": "local",
            "name": "webdav-test-library",
            "config": {"root_path": str(storage_root)},
        },
    )
    assert connection.status_code == 201, connection.text
    keys = build_user_access_key_service_from_state(fastapi_app.state)
    key = await keys.ensure_default_key(user_id)
    secret = keys.decrypt_secret_str(key)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fastapi_app),
        base_url=f"https://{Settings.root_url}",
    ) as root:
        yield Dav(root, _basic(key.access_key_id, secret), key.access_key_id, secret)


def _responses(response: httpx.Response) -> dict[str, ElementTree.Element]:
    tree = ElementTree.fromstring(response.content)  # noqa: S314
    return {
        r.findtext("D:href", namespaces=NS): r
        for r in tree.findall("D:response", NS)
    }


def _prop(entry: ElementTree.Element, name: str) -> str | None:
    return entry.findtext(f".//D:prop/D:{name}", namespaces=NS)


# -- auth / options -----------------------------------------------------


async def test_unauthenticated_gets_basic_challenge(dav: Dav) -> None:
    response = await dav.http.request("PROPFIND", f"{PREFIX}/")
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic ")


async def test_options_advertises_class_2(dav: Dav) -> None:
    response = await dav.http.request("OPTIONS", f"{PREFIX}/")
    assert response.status_code == 200
    assert response.headers["dav"] == "1, 2"
    assert "PROPFIND" in response.headers["allow"]
    assert response.headers["ms-author-via"] == "DAV"


async def test_wrong_secret_and_login_password_are_rejected(dav: Dav) -> None:
    wrong = await dav.http.request(
        "PROPFIND", f"{PREFIX}/", headers=_basic(dav.key_id, "nope"),
    )
    login = await dav.http.request(
        "PROPFIND",
        f"{PREFIX}/",
        headers=_basic("admin@example.com", "a secure first password"),
    )
    assert wrong.status_code == login.status_code == 401


async def test_malformed_basic_header_is_401(dav: Dav) -> None:
    response = await dav.http.request(
        "PROPFIND", f"{PREFIX}/", headers={"Authorization": "Basic !!!"},
    )
    assert response.status_code == 401


async def test_bearer_session_token_works(
    dav: Dav, client: httpx.AsyncClient,
) -> None:
    login = await client.post(
        "/auth/sessions",
        json={"email": "admin@example.com", "password": "a secure first password"},
    )
    token = login.json()["access_token"]
    response = await dav.http.request(
        "PROPFIND",
        f"{PREFIX}/",
        headers={"Authorization": f"Bearer {token}", "Depth": "0"},
    )
    assert response.status_code == 207, response.text
    bad = await dav.http.request(
        "PROPFIND", f"{PREFIX}/", headers={"Authorization": "Bearer junk"},
    )
    assert bad.status_code == 401


# -- the verbs ----------------------------------------------------------


async def test_mkcol_put_get_head_and_propfind(dav: Dav) -> None:
    assert (await dav.call("MKCOL", "/docs")).status_code == 201
    assert (await dav.call("MKCOL", "/docs")).status_code == 405
    assert (await dav.call("MKCOL", "/nope/child")).status_code == 409
    assert (await dav.call("MKCOL", "/x", content=b"<x/>")).status_code == 415

    body = b"hello webdav"
    created = await dav.call(
        "PUT", "/docs/hello.txt", content=body,
        headers={"Content-Type": "text/plain"},
    )
    assert created.status_code == 201, created.text

    got = await dav.call("GET", "/docs/hello.txt")
    assert got.status_code == 200
    assert got.content == body
    assert got.headers["content-type"].startswith("text/plain")
    assert got.headers["accept-ranges"] == "bytes"

    head = await dav.call("HEAD", "/docs/hello.txt")
    assert head.status_code == 200
    assert head.headers["content-length"] == str(len(body))
    assert head.content == b""

    listing = await dav.call("PROPFIND", "/docs", headers={"Depth": "1"})
    assert listing.status_code == 207
    entries = _responses(listing)
    assert f"{PREFIX}/docs/" in entries
    file_entry = entries[f"{PREFIX}/docs/hello.txt"]
    assert _prop(file_entry, "getcontentlength") == str(len(body))
    assert _prop(file_entry, "getlastmodified").endswith("GMT")
    assert _prop(file_entry, "creationdate").endswith("Z")
    assert _prop(file_entry, "getetag").startswith('"')
    assert _prop(file_entry, "getcontenttype").startswith("text/plain")
    assert file_entry.find(".//D:resourcetype/D:collection", NS) is None
    folder = entries[f"{PREFIX}/docs/"]
    assert folder.find(".//D:resourcetype/D:collection", NS) is not None


async def test_put_overwrite_returns_204_and_replaces_bytes(dav: Dav) -> None:
    await dav.call("PUT", "/over.txt", content=b"one")
    again = await dav.call("PUT", "/over.txt", content=b"second version")
    assert again.status_code == 204
    assert (await dav.call("GET", "/over.txt")).content == b"second version"


async def test_put_preconditions_and_collection_conflicts(dav: Dav) -> None:
    await dav.call("PUT", "/pre.txt", content=b"x")
    assert (
        await dav.call("PUT", "/pre.txt", content=b"y",
                       headers={"If-None-Match": "*"})
    ).status_code == 412
    assert (
        await dav.call("PUT", "/pre.txt", content=b"y",
                       headers={"If-Match": '"stale"'})
    ).status_code == 412
    assert (
        await dav.call("PUT", "/missing-parent/f.txt", content=b"y")
    ).status_code == 409
    await dav.call("MKCOL", "/adir")
    assert (await dav.call("PUT", "/adir", content=b"y")).status_code == 405
    assert (await dav.call("PUT", "/", content=b"y")).status_code == 405


async def test_empty_file_put(dav: Dav) -> None:
    assert (await dav.call("PUT", "/empty.txt", content=b"")).status_code == 201
    assert (await dav.call("GET", "/empty.txt")).content == b""


async def test_range_requests(dav: Dav) -> None:
    await dav.call("PUT", "/range.bin", content=b"0123456789")
    part = await dav.call("GET", "/range.bin", headers={"Range": "bytes=2-5"})
    assert part.status_code == 206
    assert part.content == b"2345"
    assert part.headers["content-range"] == "bytes 2-5/10"
    tail = await dav.call("GET", "/range.bin", headers={"Range": "bytes=-3"})
    assert tail.content == b"789"
    openend = await dav.call("GET", "/range.bin", headers={"Range": "bytes=7-"})
    assert openend.content == b"789"
    bad = await dav.call("GET", "/range.bin", headers={"Range": "bytes=50-60"})
    assert bad.status_code == 416
    assert bad.headers["content-range"] == "bytes */10"


async def test_conditional_get_and_missing(dav: Dav) -> None:
    await dav.call("PUT", "/cond.txt", content=b"abc")
    first = await dav.call("GET", "/cond.txt")
    again = await dav.call(
        "GET", "/cond.txt", headers={"If-None-Match": first.headers["etag"]},
    )
    assert again.status_code == 304
    assert (await dav.call("GET", "/nothing.txt")).status_code == 404
    assert (await dav.call("GET", "/docs")).status_code == 405


async def test_unicode_names_are_encoded_in_hrefs(dav: Dav) -> None:
    name = "سلام دنیا.txt"
    encoded = quote(name, safe="")
    assert (
        await dav.call("PUT", f"/{encoded}", content=b"salam")
    ).status_code == 201
    listing = await dav.call("PROPFIND", "/", headers={"Depth": "1"})
    assert f"{PREFIX}/{encoded}" in _responses(listing)
    assert (await dav.call("GET", f"/{encoded}")).content == b"salam"


async def test_hidden_dot_names_survive(dav: Dav) -> None:
    assert (await dav.call("PUT", "/.DS_Store", content=b"x")).status_code == 201
    listing = await dav.call("PROPFIND", "/", headers={"Depth": "1"})
    assert f"{PREFIX}/.DS_Store" in _responses(listing)


async def test_propfind_depth_rules_and_body_variants(dav: Dav) -> None:
    infinity = await dav.call("PROPFIND", "/", headers={"Depth": "infinity"})
    assert infinity.status_code == 403
    assert b"propfind-finite-depth" in infinity.content
    assert (await dav.call("PROPFIND", "/", headers={"Depth": "9"})).status_code == 400
    assert (await dav.call("PROPFIND", "/nothing")).status_code == 404

    zero = await dav.call("PROPFIND", "/docs", headers={"Depth": "0"})
    assert list(_responses(zero)) == [f"{PREFIX}/docs/"]

    ask = (
        b'<?xml version="1.0"?><D:propfind xmlns:D="DAV:" xmlns:Z="urn:z">'
        b"<D:prop><D:getetag/><Z:thing/></D:prop></D:propfind>"
    )
    selected = await dav.call(
        "PROPFIND", "/docs/hello.txt", content=ask, headers={"Depth": "0"},
    )
    entry = _responses(selected)[f"{PREFIX}/docs/hello.txt"]
    statuses = [s.text for s in entry.findall(".//D:status", NS)]
    assert "HTTP/1.1 200 OK" in statuses
    assert "HTTP/1.1 404 Not Found" in statuses

    names = await dav.call(
        "PROPFIND", "/docs/hello.txt", headers={"Depth": "0"},
        content=b'<D:propfind xmlns:D="DAV:"><D:propname/></D:propfind>',
    )
    assert b"<D:getetag/>" in names.content

    malformed = await dav.call("PROPFIND", "/", content=b"<nope")
    assert malformed.status_code == 400
    entity = await dav.call(
        "PROPFIND", "/", content=b'<!DOCTYPE x [<!ENTITY a "b">]><x/>',
    )
    assert entity.status_code == 400


async def test_proppatch_stub(dav: Dav) -> None:
    body = (
        b'<D:propertyupdate xmlns:D="DAV:" xmlns:Z="urn:z"><D:set><D:prop>'
        b"<Z:Win32FileAttributes>20</Z:Win32FileAttributes>"
        b"<D:getlastmodified>x</D:getlastmodified></D:prop></D:set>"
        b"</D:propertyupdate>"
    )
    response = await dav.call("PROPPATCH", "/docs/hello.txt", content=body)
    assert response.status_code == 207
    entry = _responses(response)[f"{PREFIX}/docs/hello.txt"]
    codes = [s.text for s in entry.findall(".//D:status", NS)]
    assert codes == ["HTTP/1.1 200 OK", "HTTP/1.1 403 Forbidden"]
    assert (await dav.call("PROPPATCH", "/nope", content=body)).status_code == 404


async def test_move_rename_and_into_folder(dav: Dav) -> None:
    await dav.call("PUT", "/mv.txt", content=b"mv")
    await dav.call("MKCOL", "/mvdir")
    renamed = await dav.call(
        "MOVE", "/mv.txt", headers={"Destination": dav.destination("/mv2.txt")},
    )
    assert renamed.status_code == 201
    assert (await dav.call("GET", "/mv.txt")).status_code == 404
    moved = await dav.call(
        "MOVE", "/mv2.txt",
        headers={"Destination": dav.destination("/mvdir/mv3.txt")},
    )
    assert moved.status_code == 201
    assert (await dav.call("GET", "/mvdir/mv3.txt")).content == b"mv"


async def test_move_and_copy_overwrite_semantics(dav: Dav) -> None:
    await dav.call("PUT", "/a.txt", content=b"A")
    await dav.call("PUT", "/b.txt", content=b"B")
    refused = await dav.call(
        "COPY", "/a.txt",
        headers={"Destination": dav.destination("/b.txt"), "Overwrite": "F"},
    )
    assert refused.status_code == 412
    replaced = await dav.call(
        "COPY", "/a.txt", headers={"Destination": dav.destination("/b.txt")},
    )
    assert replaced.status_code == 204
    assert (await dav.call("GET", "/b.txt")).content == b"A"
    assert (await dav.call("GET", "/a.txt")).content == b"A"


async def test_copy_folder_recursively_and_shallow(dav: Dav) -> None:
    await dav.call("MKCOL", "/tree")
    await dav.call("MKCOL", "/tree/sub")
    await dav.call("PUT", "/tree/one.txt", content=b"1")
    await dav.call("PUT", "/tree/sub/two.txt", content=b"2")
    deep = await dav.call(
        "COPY", "/tree", headers={"Destination": dav.destination("/tree-copy")},
    )
    assert deep.status_code == 201
    assert (await dav.call("GET", "/tree-copy/sub/two.txt")).content == b"2"
    assert (await dav.call("GET", "/tree-copy/one.txt")).content == b"1"
    shallow = await dav.call(
        "COPY", "/tree",
        headers={"Destination": dav.destination("/tree-shallow"), "Depth": "0"},
    )
    assert shallow.status_code == 201
    listing = await dav.call("PROPFIND", "/tree-shallow", headers={"Depth": "1"})
    assert len(_responses(listing)) == 1


async def test_move_destination_guards(dav: Dav) -> None:
    await dav.call("PUT", "/g.txt", content=b"g")
    await dav.call("MKCOL", "/gdir")
    cases = {
        "elsewhere": (
            f"https://{Settings.root_url}/api/v1/files", 502,
        ),
        "other host": (f"https://evil.example{PREFIX}/x.txt", 502),
        "same": (f"https://{Settings.root_url}{PREFIX}/g.txt", 403),
        "no parent": (f"https://{Settings.root_url}{PREFIX}/zz/g.txt", 409),
        "traversal": (f"https://{Settings.root_url}{PREFIX}/../../etc", 400),
    }
    for label, (destination, status) in cases.items():
        response = await dav.call(
            "MOVE", "/g.txt", headers={"Destination": destination},
        )
        assert response.status_code == status, label
    into_self = await dav.call(
        "MOVE", "/gdir", headers={"Destination": dav.destination("/gdir/inner")},
    )
    assert into_self.status_code == 403
    assert (await dav.call("MOVE", "/g.txt")).status_code == 400
    assert (
        await dav.call("MOVE", "/", headers={"Destination": dav.destination("/r")})
    ).status_code == 403


async def test_delete_moves_to_trash(
    dav: Dav, client: httpx.AsyncClient,
) -> None:
    await dav.call("MKCOL", "/trashme")
    await dav.call("PUT", "/trashme/in.txt", content=b"in")
    assert (await dav.call("DELETE", "/trashme")).status_code == 204
    assert (await dav.call("GET", "/trashme/in.txt")).status_code == 404
    assert (await dav.call("DELETE", "/trashme")).status_code == 404
    assert (await dav.call("DELETE", "/")).status_code == 403
    login = await client.post(
        "/auth/sessions",
        json={"email": "admin@example.com", "password": "a secure first password"},
    )
    trash = await client.get(
        "/files",
        params={"scope": "trash"},
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert "trashme" in [item["name"] for item in trash.json()["items"]]


async def test_duplicate_library_names_get_suffixes(
    dav: Dav, client: httpx.AsyncClient,
) -> None:
    login = await client.post(
        "/auth/sessions",
        json={"email": "admin@example.com", "password": "a secure first password"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    for _ in range(2):
        folder = await client.post(
            "/files", data={"name": "twin", "type": "folder"}, headers=headers,
        )
        assert folder.status_code == 201, folder.text
    listing = await dav.call("PROPFIND", "/", headers={"Depth": "1"})
    hrefs = set(_responses(listing))
    assert {f"{PREFIX}/twin/", f"{PREFIX}/twin%20%282%29/"} <= hrefs


async def test_locking_blocks_other_writers_until_token(dav: Dav) -> None:
    await dav.call("PUT", "/locked.txt", content=b"v1")
    lock_body = (
        b'<D:lockinfo xmlns:D="DAV:"><D:lockscope><D:exclusive/></D:lockscope>'
        b"<D:locktype><D:write/></D:locktype><D:owner>me</D:owner></D:lockinfo>"
    )
    locked = await dav.call(
        "LOCK", "/locked.txt", content=lock_body, headers={"Timeout": "Second-60"},
    )
    assert locked.status_code == 200, locked.text
    token = locked.headers["lock-token"].strip("<>")
    assert b"opaquelocktoken" in locked.content
    assert (
        await dav.call("PUT", "/locked.txt", content=b"v2")
    ).status_code == 423
    assert (await dav.call("DELETE", "/locked.txt")).status_code == 423
    assert (
        await dav.call("LOCK", "/locked.txt", content=lock_body)
    ).status_code == 423
    seen = await dav.call("PROPFIND", "/locked.txt", headers={"Depth": "0"})
    assert token.encode() in seen.content
    ok = await dav.call(
        "PUT", "/locked.txt", content=b"v2", headers={"If": f"(<{token}>)"},
    )
    assert ok.status_code == 204
    refreshed = await dav.call("LOCK", "/locked.txt", headers={"If": f"(<{token}>)"})
    assert refreshed.status_code == 200
    wrong = await dav.call("UNLOCK", "/locked.txt",
                           headers={"Lock-Token": "<opaquelocktoken:nope>"})
    assert wrong.status_code == 409
    gone = await dav.call("UNLOCK", "/locked.txt",
                          headers={"Lock-Token": f"<{token}>"})
    assert gone.status_code == 204
    assert (await dav.call("DELETE", "/locked.txt")).status_code == 204


async def test_lock_null_resource_then_put(dav: Dav) -> None:
    body = (
        b'<D:lockinfo xmlns:D="DAV:"><D:lockscope><D:exclusive/></D:lockscope>'
        b"<D:locktype><D:write/></D:locktype></D:lockinfo>"
    )
    locked = await dav.call("LOCK", "/fresh.txt", content=body)
    assert locked.status_code == 201
    token = locked.headers["lock-token"].strip("<>")
    put = await dav.call(
        "PUT", "/fresh.txt", content=b"x", headers={"If": f"(<{token}>)"},
    )
    assert put.status_code == 201
    assert (
        await dav.call("LOCK", "/no/such/parent.txt", content=body)
    ).status_code == 409
    assert (await dav.call("LOCK", "/fresh.txt", content=b"<bad")).status_code == 400
    assert (await dav.call("LOCK", "/fresh.txt", headers={"If": "(<x>)"})
            ).status_code == 412


async def test_upload_size_cap(dav: Dav, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Settings, "WEBDAV_MAX_UPLOAD_BYTES", 8)
    assert (
        await dav.call("PUT", "/big.bin", content=b"x" * 64)
    ).status_code == 413
    assert (await dav.call("GET", "/big.bin")).status_code == 404

    async def chunks() -> object:
        yield b"12345"
        yield b"67890"  # no Content-Length: the received-bytes check trips

    chunked = await dav.call("PUT", "/big2.bin", content=chunks())
    assert chunked.status_code == 413


async def test_path_traversal_segments_are_rejected(dav: Dav) -> None:
    response = await dav.call("PUT", "/a/%2e%2e/%2e%2e/etc/passwd", content=b"x")
    assert response.status_code in {400, 409}
    assert (await dav.call("GET", "/%2e%2e/secret")).status_code == 400


async def test_disabled_gateway_is_404(
    dav: Dav, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Settings, "WEBDAV_ENABLED", False)
    assert (await dav.call("PROPFIND", "/")).status_code == 404


async def test_forwarded_prefix_shapes_hrefs(dav: Dav) -> None:
    response = await dav.call(
        "PROPFIND", "/docs", headers={"Depth": "0", "X-Forwarded-Prefix": "/umedia"},
    )
    assert f"/umedia{PREFIX}/docs/" in _responses(response)


# -- pure units ---------------------------------------------------------


def test_clean_segment_rules() -> None:
    assert clean_segment(".hidden") == ".hidden"
    for bad in ("", ".", "..", "a/b", "a\\b", "a\x00b"):
        with pytest.raises(BadPathError):
            clean_segment(bad)


def test_mount_and_destination_paths() -> None:
    assert mount_relative("/dav", "/dav") == ""
    assert mount_relative("/dav/a/b", "/dav") == "a/b"
    assert mount_relative("/davx/a", "/dav") is None
    assert destination_path("/dav/a%20b", host=None, base="/dav") == "a b"
    assert destination_path("http://h/dav/x", host="h", base="/dav") == "x"
    assert destination_path("http://o/dav/x", host="h", base="/dav") is None
    assert href("/dav", ["a b", "ن"], collection=True) == "/dav/a%20b/%D9%86/"
    assert href("/dav", [], collection=True) == "/dav/"


def test_byte_range_edges() -> None:
    assert byte_range(None, 10) is None
    assert byte_range("bytes=0-1,4-5", 10) is None
    assert byte_range("bytes=abc", 10) is None
    assert byte_range("bytes=0-99", 10) == (0, 9)
    assert byte_range("bytes=2-3", 0) is None
    with pytest.raises(DavError):
        byte_range("bytes=20-", 10)


def test_lock_table_scoping() -> None:
    table = LockTable()
    table.create(user_id="u", path="/a", depth_infinity=True, owner_xml="",
                 timeout=60)
    assert table.covering("u", "/a/b/c", subtree=False)
    assert not table.covering("other", "/a/b", subtree=False)
    assert table.covering("u", "/", subtree=True) == []
    assert table.covering("u", "", subtree=True)
    table.remove_under("u", "/a")
    assert not table.covering("u", "/a", subtree=False)
    assert submitted_tokens("(<opaquelocktoken:1>)", "<opaquelocktoken:2>") == {
        "opaquelocktoken:1",
        "opaquelocktoken:2",
    }
