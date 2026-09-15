"""Conflict-aware library display names for transfers.

Reuses the S3 gateway's `copy_suffix` shape (`stem (N).ext`) and adds a
first-collision ` (copy)` variant for transfer UX.
"""

from apps.s3.paths import copy_suffix


def with_name_suffix(name: str, suffix: str) -> str:
    """Insert `suffix` before the last extension, like `copy_suffix`."""
    stem, separator, extension = name.rpartition(".")
    if separator and stem:
        return f"{stem}{suffix}.{extension}"
    return f"{name}{suffix}"


def resolve_conflict_name(name: str, claimed: set[str]) -> str:
    """Pick a free sibling name: original, then ` (copy)`, then `(2)`…"""
    if name not in claimed:
        return name
    candidate = with_name_suffix(name, " (copy)")
    if candidate not in claimed:
        return candidate
    index = 2
    while True:
        candidate = copy_suffix(name, index)
        if candidate not in claimed:
            return candidate
        index += 1
