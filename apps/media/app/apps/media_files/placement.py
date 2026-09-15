"""Where a new library file or folder should land.

Folders are bound to one provider connection. Uploads into a folder
follow that binding. Root placement (no parent, or an unbound ancestor
chain) uses the instance policy from settings.
"""

from dataclasses import dataclass, field
from typing import Literal

from apps.media_files.errors import MediaFileValidationError

PlacementPolicy = Literal["default", "fill_order", "most_free"]

POLICIES: tuple[PlacementPolicy, ...] = ("default", "fill_order", "most_free")


@dataclass(frozen=True)
class PlacementSettings:
    policy: PlacementPolicy = "default"
    default_connection_id: str | None = None
    fill_order: list[str] = field(default_factory=list)


def pick_connection(
    *,
    settings: PlacementSettings,
    enabled_ids: list[str],
    parent_connection_id: str | None = None,
    preferred_connection_id: str | None = None,
) -> str:
    """Pick one enabled connection.

    A bound parent folder always wins. At the library root (or an
    unbound parent) an explicit `preferred_connection_id` wins if it is
    still enabled; otherwise the instance policy applies. `most_free`
    falls back to fill-order then default until plugins report real
    free space.
    """
    enabled = [uid for uid in enabled_ids if uid]
    enabled_set = set(enabled)
    if parent_connection_id:
        if parent_connection_id not in enabled_set:
            raise MediaFileValidationError(
                "This folder's storage is disabled or missing",
            )
        return parent_connection_id
    if not enabled:
        raise MediaFileValidationError("No storage is available for upload")
    if preferred_connection_id and preferred_connection_id in enabled_set:
        return preferred_connection_id

    if settings.policy in {"fill_order", "most_free"}:
        for uid in settings.fill_order:
            if uid in enabled_set:
                return uid

    if (
        settings.default_connection_id
        and settings.default_connection_id in enabled_set
    ):
        return settings.default_connection_id
    return enabled[0]
