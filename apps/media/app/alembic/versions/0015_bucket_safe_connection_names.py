"""Make every connection name a valid S3 bucket name.

Each connection is now its own bucket in the owner's S3 API, so free-text
names like "Microsoft OneDrive" become "microsoft-onedrive". Names are
made unique per owner (`-2`, `-3`, ...). Soft-deleted rows are skipped:
they are never listed as buckets. The old name is kept in `meta_data`
under `display_name_before_0015` so the change can be reverted by hand.

Downgrade restores those saved names.
"""

import json

import sqlalchemy as sa

from alembic import op
from apps.provider_connections.names import (
    InvalidConnectionName,
    slugify_connection_name,
    unique_connection_name,
    validate_connection_name,
)

revision = "0015_bucket_safe_connection_names"
down_revision = "0014_operation_notifications"
branch_labels = None
depends_on = None

_BACKUP_KEY = "display_name_before_0015"


def _is_valid(name: str) -> bool:
    try:
        validate_connection_name(name)
    except InvalidConnectionName:
        return False
    return True


def _meta(raw: object) -> dict:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw:
        loaded = json.loads(raw)
        return loaded if isinstance(loaded, dict) else {}
    return {}


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT uid, owner_id, name, meta_data FROM provider_connections "
            "WHERE is_deleted = 0 ORDER BY created_at",
        ),
    ).fetchall()

    # Already-valid names keep priority (the oldest row wins a duplicate);
    # every other row is renamed around them.
    taken: dict[str, set[str]] = {}
    keeps: set[str] = set()
    for row in rows:
        owner_taken = taken.setdefault(row.owner_id, set())
        if _is_valid(row.name) and row.name not in owner_taken:
            owner_taken.add(row.name)
            keeps.add(row.uid)

    for row in rows:
        if row.uid in keeps:
            continue
        owner_taken = taken[row.owner_id]
        new_name = unique_connection_name(
            slugify_connection_name(row.name), owner_taken
        )
        owner_taken.add(new_name)
        meta = _meta(row.meta_data)
        meta[_BACKUP_KEY] = row.name
        bind.execute(
            sa.text(
                "UPDATE provider_connections SET name = :name, meta_data = :meta "
                "WHERE uid = :uid",
            ),
            {"name": new_name, "meta": json.dumps(meta), "uid": row.uid},
        )


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT uid, meta_data FROM provider_connections"),
    ).fetchall()
    for row in rows:
        meta = _meta(row.meta_data)
        if _BACKUP_KEY not in meta:
            continue
        original = meta.pop(_BACKUP_KEY)
        bind.execute(
            sa.text(
                "UPDATE provider_connections SET name = :name, meta_data = :meta "
                "WHERE uid = :uid",
            ),
            {
                "name": original,
                "meta": json.dumps(meta) if meta else None,
                "uid": row.uid,
            },
        )
