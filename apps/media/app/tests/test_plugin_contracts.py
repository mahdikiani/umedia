"""Unit tests for `plugins.contracts`' URL-path resource-id encoding --
see `encode_resource_id`'s docstring for why this exists (a `/`-bearing id
can't be a plain `{resource_id}` path segment, and percent-encoding the
slash doesn't help either)."""

import pytest

from plugins.contracts import (
    decode_header_json,
    decode_resource_id,
    encode_header_json,
    encode_resource_id,
)


@pytest.mark.parametrize(
    "resource_id",
    [
        "hello.txt",
        "a-folder/nested.txt",
        "a/b/c/d.txt",
        "",
        "id with spaces & % weirdness",
        "aGVsbG8=",  # already base64-shaped, must not confuse the decoder
    ],
)
def test_encode_decode_round_trips(resource_id: str) -> None:
    assert decode_resource_id(encode_resource_id(resource_id)) == resource_id


def test_encoded_form_never_contains_a_slash() -> None:
    assert "/" not in encode_resource_id("a/b/c.txt")


def test_header_json_is_ascii_even_for_persian_filenames() -> None:
    """httpx encodes HTTP headers as ASCII. A raw JSON name like
    `سیدیوسف_درسته-fa.pdf` is what produced Cyberduck's
    `'ascii' codec can't encode characters in position 9-15`."""
    payload = {"name": "سیدیوسف_درسته-fa.pdf", "type": "file", "parent_id": None}
    encoded = encode_header_json(payload)
    encoded.encode("ascii")
    assert decode_header_json(encoded) == payload


def test_header_json_still_accepts_plain_json() -> None:
    assert decode_header_json('{"root_path":"/storage"}') == {
        "root_path": "/storage",
    }
