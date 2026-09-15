"""Legacy Temporary-as-folder (`metadata.staging`). Pointer clipboard replaced it."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .records import MediaFileRecord


def is_legacy_staging_folder(record: MediaFileRecord) -> bool:
    return (
        record.type == "folder"
        and (record.metadata or {}).get("staging") is True
    )
