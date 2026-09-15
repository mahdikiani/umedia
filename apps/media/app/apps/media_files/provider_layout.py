"""Where core places bytes on path-addressed providers.

Plugins stay adapters. A `local` connection is instance-level (one
mounted root), but uploads are namespaced per owning user so two
library owners cannot overwrite the same disk path
(`docs/12-file-storage.md`).
"""

UMEDIA_MANAGED_PREFIX = ".umedia"
LOCAL_USER_DUMP_PREFIX = f"{UMEDIA_MANAGED_PREFIX}/users"
LOCAL_PROVIDER_TYPE = "local"


def is_umedia_managed_reference(content_reference: str | None) -> bool:
    """True for core-owned dump paths that inbound sync must not import."""
    if not content_reference:
        return False
    return (
        content_reference == UMEDIA_MANAGED_PREFIX
        or content_reference.startswith(f"{UMEDIA_MANAGED_PREFIX}/")
    )


def _segment(value: str) -> str:
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError(f"unsafe path segment: {value!r}")
    return value


def local_upload_parent(*, owner_id: str, media_file_uid: str) -> str:
    return (
        f"{LOCAL_USER_DUMP_PREFIX}/{_segment(owner_id)}/{_segment(media_file_uid)}"
    )


def plugin_upload_parent(
    *,
    provider_type: str | None,
    owner_id: str,
    media_file_uid: str,
) -> str | None:
    """`CreateResourceIn.parent_id` for a new upload, or `None` to write
    at the provider root (opaque-id remotes such as Telegram/S3)."""
    if provider_type == LOCAL_PROVIDER_TYPE:
        return local_upload_parent(
            owner_id=owner_id, media_file_uid=media_file_uid,
        )
    return None
