"""Pure helpers: URL path <-> library path segments, dates, etags, hrefs."""

import unicodedata
from datetime import UTC, datetime
from email.utils import format_datetime
from urllib.parse import quote, unquote, urlsplit

from apps.media_files.schemas import MediaFileRecord


class BadPathError(ValueError):
    """The request path is not a valid library path (traversal, NUL, ...)."""


def clean_segment(segment: str) -> str:
    """One library name from one decoded URL segment.

    Unlike the S3 projection this keeps leading dots (`.DS_Store`, `._x`
    are what Finder writes) and only rejects what could escape the tree.
    """
    name = unicodedata.normalize("NFC", segment)
    if name in {"", ".", ".."} or any(c in name for c in "/\\\x00"):
        raise BadPathError(segment)
    return name


def split_path(decoded: str) -> list[str]:
    """Decoded path (below the mount) -> validated segments."""
    return [clean_segment(part) for part in decoded.split("/") if part]


def mount_relative(path: str, base: str) -> str | None:
    """`path` below mount `base`, or None when it is outside the mount."""
    if path == base:
        return ""
    if path.startswith(base + "/"):
        return path[len(base) + 1:]
    return None


def destination_path(header: str, *, host: str | None, base: str) -> str | None:
    """Decoded path below the mount for a `Destination` header, or None
    when it points elsewhere (other host, other prefix)."""
    parts = urlsplit(header)
    if parts.netloc and host and parts.netloc.lower() != host.lower():
        return None
    return mount_relative(unquote(parts.path), base)


def href(base: str, segments: list[str], *, collection: bool) -> str:
    path = "/".join(quote(segment, safe="") for segment in segments)
    full = f"{base}/{path}" if path else f"{base}/"
    if collection and not full.endswith("/"):
        full += "/"
    return full


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def http_date(value: datetime) -> str:
    return format_datetime(as_utc(value), usegmt=True)


def iso_date(value: datetime) -> str:
    return as_utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def etag(record: MediaFileRecord) -> str:
    if record.type == "folder":
        return f'"{record.uid}-{int(as_utc(record.updated_at).timestamp())}"'
    return f'"{record.content_hash or record.uid}"'
