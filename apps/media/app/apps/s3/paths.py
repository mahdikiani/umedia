"""Pure MediaFile library-path projection for the S3 gateway."""

import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from urllib.parse import quote, unquote


class LibraryNode(Protocol):
    uid: str
    name: str
    parent_id: str | None
    type: str
    created_at: datetime
    is_deleted: bool


def safe_filename(name: str) -> str:
    """A single path segment for object keys -- no slashes, never empty.

    `#` / `?` / `&` are not stripped; `encode_key` percent-encodes them.

    NFKC turns macOS narrow no-break spaces (U+202F in screenshot names)
    into ASCII spaces so a plugin with an ASCII filesystem encoding can
    still create the file.
    """
    cleaned = (
        unicodedata.normalize("NFKC", unquote(name))
        .replace("/", "_")
        .replace("\\", "_")
        .replace("\x00", "")
        .strip()
        .strip(".")
    )
    return cleaned or "file"


def copy_suffix(name: str, index: int) -> str:
    """index 1 keeps the name; later copies insert before the last extension."""
    if index <= 1:
        return name
    stem, separator, extension = name.rpartition(".")
    if separator and stem:
        return f"{stem} ({index}).{extension}"
    return f"{name} ({index})"


def encode_key(decoded_key: str) -> str:
    """Normalize and percent-encode every non-empty path segment."""
    return "/".join(
        quote(safe_filename(unquote(segment)), safe="._-()")
        for segment in decoded_key.split("/")
        if segment
    )


def decode_key(key: str) -> str:
    """Unquote and normalize every non-empty path segment."""
    return "/".join(
        safe_filename(unquote(segment))
        for segment in key.split("/")
        if segment
    )


def _unique_segment(natural: str, claimed: set[str]) -> str:
    """Oldest sibling keeps `natural` when free; later copies skip taken suffixes."""
    if natural not in claimed:
        return natural
    index = 2
    while True:
        candidate = copy_suffix(natural, index)
        if candidate not in claimed:
            return candidate
        index += 1


def _projected_paths(records: Sequence[LibraryNode]) -> dict[str, str]:
    active = [record for record in records if not record.is_deleted]
    by_uid = {record.uid: record for record in active}
    siblings: dict[str | None, list[LibraryNode]] = defaultdict(list)
    for record in active:
        siblings[record.parent_id].append(record)

    segments: dict[str, str] = {}
    for group in siblings.values():
        claimed: set[str] = set()
        for record in sorted(group, key=lambda item: (item.created_at, item.uid)):
            segment = _unique_segment(safe_filename(record.name), claimed)
            claimed.add(segment)
            segments[record.uid] = segment

    projected: dict[str, str] = {}
    for record in active:
        path_segments = [segments[record.uid]]
        visited = {record.uid}
        parent_id = record.parent_id
        while parent_id is not None and parent_id not in visited:
            parent = by_uid.get(parent_id)
            if parent is None:
                break
            visited.add(parent.uid)
            path_segments.append(segments[parent.uid])
            parent_id = parent.parent_id
        projected[record.uid] = "/".join(reversed(path_segments))
    return projected


def file_keys(records: Sequence[LibraryNode]) -> dict[str, str]:
    """Return file uid to encoded projected S3 object key."""
    paths = _projected_paths(records)
    return {
        record.uid: encode_key(paths[record.uid])
        for record in records
        if not record.is_deleted and record.type == "file"
    }


def folder_prefixes(records: Sequence[LibraryNode]) -> dict[str, str]:
    """Return folder uid to encoded projected prefix ending in a slash."""
    paths = _projected_paths(records)
    return {
        record.uid: f"{encode_key(paths[record.uid])}/"
        for record in records
        if not record.is_deleted and record.type == "folder"
    }
