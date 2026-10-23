"""MediaFileService: Sharing operations (mixed into `MediaFileService`)."""

import dataclasses
import time
from typing import Any
from urllib.parse import urlparse

from apps.s3.auth import generate_presigned_url
from apps.s3.paths import file_keys

from .errors import (
    MediaFileStateError,
    MediaFileValidationError,
)
from .permissions import (
    PermissionEnum,
    can_read,
)
from .schemas import (
    MediaFileRecord,
)
from .shared import (
    PUBLIC_PERMISSIONS,
    TEMPORARY_LINK_MAX_SECONDS,
    TEMPORARY_LINK_MIN_SECONDS,
    TemporaryLink,
)


class SharingMixin:
    async def set_public_permission(
        self,
        uid: str,
        value: str,
        *,
        actor_user_id: str,
    ) -> MediaFileRecord:
        if value not in PUBLIC_PERMISSIONS:
            raise MediaFileValidationError(
                f"Invalid public_permission '{value}', must be one of "
                f"{sorted(PUBLIC_PERMISSIONS)}",
            )
        record = await self.get(uid, actor_user_id=actor_user_id)
        self._require(
            record,
            actor_user_id,
            PermissionEnum.MANAGE,
            "change sharing of",
        )
        return await self._files.update(uid, {"public_permission": value})

    async def create_temporary_link(
        self,
        uid: str,
        *,
        actor_user_id: str,
        expires_in: int,
        base_url: str,
    ) -> TemporaryLink:
        """Mint a SigV4-presigned GET for the projected S3 library path,
        signed with the *actor's* default access key (created on the spot for
        accounts that predate access keys). Same MANAGE gate as
        `set_public_permission`. Deactivating that key revokes every URL
        it signed. A later rename changes future mints and invalidates the
        old projected path."""
        if self._access_keys is None:
            raise MediaFileStateError(
                "Temporary links are not configured (no access-key service)",
            )
        if not (TEMPORARY_LINK_MIN_SECONDS <= expires_in <= TEMPORARY_LINK_MAX_SECONDS):
            raise MediaFileValidationError(
                f"expires_in must be between {TEMPORARY_LINK_MIN_SECONDS} and "
                f"{TEMPORARY_LINK_MAX_SECONDS} seconds",
            )
        record = await self.get(uid, actor_user_id=actor_user_id)
        self._require(
            record,
            actor_user_id,
            PermissionEnum.MANAGE,
            "change sharing of",
        )
        key = await self._access_keys.ensure_default_key(actor_user_id)
        secret = self._access_keys.decrypt_secret_str(key)
        expires = int(time.time()) + expires_in
        full_url = generate_presigned_url(
            method="GET",
            key=file_keys(
                await self._files.list_visible(actor_user_id=actor_user_id),
            )[uid],
            expires_in=expires_in,
            base_url=base_url,
            access_key=key.access_key_id,
            secret_key=secret,
        )
        parsed = urlparse(full_url)
        return TemporaryLink(
            url=f"{parsed.path}?{parsed.query}",
            key_id=key.access_key_id,
            expires=expires,
        )

    async def set_starred(
        self,
        uid: str,
        *,
        actor_user_id: str,
        starred: bool,
    ) -> MediaFileRecord:
        record = await self.get(uid, actor_user_id=actor_user_id)
        await self._files.set_starred(
            actor_user_id=actor_user_id,
            media_file_uid=uid,
            starred=starred,
        )
        return dataclasses.replace(record, starred=starred)

    async def add_temporary_items(
        self,
        ids: list[str],
        *,
        actor_user_id: str,
    ) -> int:
        unique_ids = list(dict.fromkeys(ids))
        for media_file_uid in unique_ids:
            await self._get_visible(
                media_file_uid,
                actor_user_id=actor_user_id,
            )

        added = 0
        for media_file_uid in unique_ids:
            was_added = await self._files.add_temporary_item(
                actor_user_id=actor_user_id,
                media_file_uid=media_file_uid,
            )
            if was_added:
                added += 1
        return added

    async def list_temporary_items(
        self,
        *,
        actor_user_id: str,
    ) -> list[MediaFileRecord]:
        records = await self._files.list_temporary_items(
            actor_user_id=actor_user_id,
        )
        visible = [record for record in records if can_read(record, actor_user_id)]
        return await self._with_starred(
            visible,
            actor_user_id=actor_user_id,
        )

    async def remove_temporary_item(
        self,
        media_file_id: str,
        *,
        actor_user_id: str,
    ) -> None:
        await self._files.remove_temporary_item(
            actor_user_id=actor_user_id,
            media_file_uid=media_file_id,
        )

    async def clear_temporary_items(self, *, actor_user_id: str) -> None:
        await self._files.clear_temporary_items(
            actor_user_id=actor_user_id,
        )

    async def set_user_permission(
        self,
        uid: str,
        *,
        actor_user_id: str,
        target_user_id: str,
        permission: int,
    ) -> MediaFileRecord:
        """Grant/replace `target_user_id`'s level on `uid`; `0` (NONE)
        removes the entry entirely."""
        record = await self.get(uid, actor_user_id=actor_user_id)
        self._require(
            record,
            actor_user_id,
            PermissionEnum.MANAGE,
            "change sharing of",
        )
        try:
            level = PermissionEnum(int(permission))
        except ValueError:
            raise MediaFileValidationError(
                f"Invalid permission {permission!r}, must be one of "
                f"{[int(member) for member in PermissionEnum]}",
            ) from None
        if target_user_id == record.owner_id:
            raise MediaFileValidationError(
                "The owner already holds full access; their permission "
                "cannot be granted or revoked",
            )
        entries = [
            entry
            for entry in record.permissions
            if entry.get("user_id") != target_user_id
        ]
        if level != PermissionEnum.NONE:
            entries.append({"user_id": target_user_id, "permission": int(level)})
        return await self._files.update(uid, {"permissions": entries})

    async def list_permissions(
        self,
        uid: str,
        *,
        actor_user_id: str,
    ) -> list[dict[str, Any]]:
        record = await self.get(uid, actor_user_id=actor_user_id)
        self._require(
            record,
            actor_user_id,
            PermissionEnum.MANAGE,
            "view sharing of",
        )
        return record.permissions
