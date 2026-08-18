from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from apps.s3.paths import (
    copy_suffix,
    decode_key,
    encode_key,
    file_keys,
    folder_prefixes,
    safe_filename,
)


@dataclass(frozen=True, slots=True)
class Node:
    uid: str
    name: str
    parent_id: str | None
    type: str
    created_at: datetime
    is_deleted: bool = False


NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_safe_filename_turns_macos_narrow_nbsp_into_a_regular_space() -> None:
    name = "Screenshot 1405-05-26 at 2.04.22\u202fPM.png"

    cleaned = safe_filename(name)

    assert cleaned == "Screenshot 1405-05-26 at 2.04.22 PM.png"


def test_safe_filename_unquotes_percent_encoded_nbsp() -> None:
    encoded = "Screenshot%201405-05-26%20at%202.04.22%E2%80%AFPM.png"

    cleaned = safe_filename(encoded)

    assert cleaned.endswith("PM.png")


def test_copy_suffix_inserts_before_last_extension() -> None:
    assert copy_suffix("archive.tar.gz", 1) == "archive.tar.gz"
    assert copy_suffix("archive.tar.gz", 2) == "archive.tar (2).gz"
    assert copy_suffix(".gitignore", 2) == ".gitignore (2)"


def test_file_keys_project_unique_library_path() -> None:
    records = [
        Node("folder", "docs", None, "folder", NOW),
        Node("file", "a.txt", "folder", "file", NOW),
    ]

    keys = file_keys(records)

    assert keys == {"file": "docs/a.txt"}


def test_file_keys_disambiguate_sibling_files_by_created_at_then_uid() -> None:
    records = [
        Node("later", "f72.txt", None, "file", NOW + timedelta(seconds=1)),
        Node("older-b", "f72.txt", None, "file", NOW),
        Node("older-a", "f72.txt", None, "file", NOW),
    ]

    keys = file_keys(records)

    assert keys == {
        "older-a": "f72.txt",
        "older-b": "f72%20(2).txt",
        "later": "f72%20(3).txt",
    }


def test_file_keys_skip_suffix_already_used_by_a_sibling() -> None:
    """A real `foo (2).txt` must not share a key with the second `foo.txt`."""
    records = [
        Node("first", "foo.txt", None, "file", NOW),
        Node("literal", "foo (2).txt", None, "file", NOW + timedelta(seconds=1)),
        Node("dup", "foo.txt", None, "file", NOW + timedelta(seconds=2)),
    ]

    keys = file_keys(records)

    assert keys == {
        "first": "foo.txt",
        "literal": "foo%20(2).txt",
        "dup": "foo%20(3).txt",
    }
    assert len(set(keys.values())) == 3


def test_projection_disambiguates_duplicate_folder_segments() -> None:
    records = [
        Node("trip-1", "سفر", None, "folder", NOW),
        Node("trip-2", "سفر", None, "folder", NOW + timedelta(seconds=1)),
        Node("file-1", "a.txt", "trip-1", "file", NOW),
        Node("file-2", "a.txt", "trip-2", "file", NOW),
    ]

    keys = file_keys(records)
    prefixes = folder_prefixes(records)

    assert decode_key(keys["file-1"]) == "سفر/a.txt"
    assert decode_key(keys["file-2"]) == "سفر (2)/a.txt"
    assert decode_key(prefixes["trip-1"]) == "سفر"
    assert decode_key(prefixes["trip-2"]) == "سفر (2)"
    assert prefixes["trip-1"].endswith("/")
    assert prefixes["trip-2"].endswith("/")


def test_encode_key_normalizes_narrow_nbsp_and_encodes_spaces() -> None:
    key = encode_key("folder/Screenshot at 2.04.22\u202fPM.png")

    assert key == "folder/Screenshot%20at%202.04.22%20PM.png"


def test_decode_key_round_trips_percent_encoded_segments() -> None:
    encoded = encode_key("سفر/a#b?.pdf")

    assert decode_key(encoded) == "سفر/a#b?.pdf"
