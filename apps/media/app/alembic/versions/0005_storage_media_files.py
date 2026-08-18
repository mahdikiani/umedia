"""Dual-layer library (docs/11-dual-layer-library.md): StorageObject +
MediaFile replace the single-`resources` coupling.

- `provider_connections` gains the two orthogonal flags
  (`import_existing`, `mirror_structure`), both defaulting off.
- New tables: `storage_objects` (physical provider index),
  `media_files` (user library; the folder tree lives only here),
  `media_file_objects` (the link, v1 = one `primary` per object).
- Data migration from `resources`: every row becomes a `media_files` row
  *keeping its uid* (so existing `/f/{uid}` share links keep working);
  rows with a `content_reference` additionally get a `storage_objects`
  row + a `primary` link. Folders become MediaFile-only, as the model
  prescribes.
- Owner backfill: on this install usso.lite's `localuser` table lives in
  the *same* SQLite database, so rows still carrying migration 0004's
  `legacy-unassigned` sentinel are reassigned to the first admin found
  there (falling back to the sentinel if no admin exists yet -- such rows
  stay invisible until an operator reassigns them, exactly as before).
- The `resources` table is deliberately NOT dropped -- the old routes are
  simply no longer mounted; a later cleanup migration removes the table.
"""

import json
import uuid
from datetime import datetime

import sqlalchemy as sa

from alembic import op

revision = "0005_storage_media_files"
down_revision = "0004_resource_acl"
branch_labels = None
depends_on = None

LEGACY_OWNER_SENTINEL = "legacy-unassigned"


def _first_admin_uid(bind: sa.engine.Connection) -> str | None:
    """The uid of the first admin in usso.lite's `localuser` table, if
    that table exists here (same-database installs) and has one. `roles`
    is a JSON list column; the check runs in Python -- a handful of rows,
    and SQLite JSON operators would just re-implement it less clearly."""
    inspector = sa.inspect(bind)
    if "localuser" not in inspector.get_table_names():
        return None
    rows = bind.execute(sa.text(
        "SELECT uid, roles FROM localuser "
        "WHERE is_deleted = 0 ORDER BY created_at",
    )).fetchall()
    for uid, roles in rows:
        try:
            parsed = json.loads(roles) if isinstance(roles, str) else roles
        except (TypeError, ValueError):
            continue
        if parsed and "admin" in parsed:
            return uid
    return None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # provider_connections: the two dual-layer flags
    # ------------------------------------------------------------------
    op.add_column(
        "provider_connections",
        sa.Column(
            "import_existing",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "provider_connections",
        sa.Column(
            "mirror_structure",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

    # ------------------------------------------------------------------
    # storage_objects: the physical provider index
    # ------------------------------------------------------------------
    op.create_table(
        "storage_objects",
        sa.Column("provider_connection_id", sa.String(), nullable=False),
        sa.Column("content_reference", sa.String(), nullable=False),
        sa.Column("provider_parent_ref", sa.String(), nullable=True),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=True),
        sa.Column("content_type", sa.String(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("uid", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("meta_data", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("uid"),
        sa.UniqueConstraint(
            "provider_connection_id",
            "content_reference",
            name="uq_storage_objects_connection_reference",
        ),
    )
    for column in (
        "provider_connection_id",
        "content_reference",
        "provider_parent_ref",
        "type",
        "name",
        "content_hash",
        "status",
        "uid",
        "created_at",
    ):
        op.create_index(
            f"ix_storage_objects_{column}",
            "storage_objects",
            [column],
            unique=column == "uid",
        )

    # ------------------------------------------------------------------
    # media_files: the user library (folder tree lives only here)
    # ------------------------------------------------------------------
    op.create_table(
        "media_files",
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("parent_id", sa.String(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("public_permission", sa.String(), nullable=False),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=True),
        sa.Column("access_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("history", sa.JSON(), nullable=False),
        sa.Column("uid", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("meta_data", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("uid"),
    )
    for column in (
        "owner_id",
        "type",
        "name",
        "parent_id",
        "status",
        "workspace_id",
        "uid",
        "created_at",
    ):
        op.create_index(
            f"ix_media_files_{column}",
            "media_files",
            [column],
            unique=column == "uid",
        )

    # ------------------------------------------------------------------
    # media_file_objects: the link (v1: primary only, unique per object)
    # ------------------------------------------------------------------
    op.create_table(
        "media_file_objects",
        sa.Column("media_file_id", sa.String(), nullable=False),
        sa.Column("storage_object_id", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("uid", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("meta_data", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("uid"),
        sa.UniqueConstraint(
            "storage_object_id",
            name="uq_media_file_objects_storage_object",
        ),
    )
    for column in ("media_file_id", "storage_object_id", "role", "uid", "created_at"):
        op.create_index(
            f"ix_media_file_objects_{column}",
            "media_file_objects",
            [column],
            unique=column == "uid",
        )

    _migrate_resources(op.get_bind())


def _migrate_resources(bind: sa.engine.Connection) -> None:
    inspector = sa.inspect(bind)
    if "resources" not in inspector.get_table_names():
        return  # fresh install: nothing to carry over

    admin_uid = _first_admin_uid(bind)
    now = datetime.now()

    rows = bind.execute(sa.text(
        "SELECT uid, provider_connection_id, owner_id, type, name, "
        "parent_id, metadata, content_reference, content_hash, "
        "content_type, size, status, error, public_permission, "
        "permissions, workspace_id, access_at, deleted_at, history, "
        "created_at, updated_at, is_deleted "
        "FROM resources",
    )).mappings().fetchall()

    media_file_rows = []
    storage_object_rows = []
    link_rows = []
    for row in rows:
        owner_id = row["owner_id"]
        if owner_id == LEGACY_OWNER_SENTINEL and admin_uid is not None:
            owner_id = admin_uid
        media_file_rows.append({
            "uid": row["uid"],  # preserved: /f/{uid} links keep working
            "owner_id": owner_id,
            "type": row["type"],
            "name": row["name"],
            # Also preserved as-is: resource parents became media file
            # uids unchanged, so the tree carries over row for row.
            "parent_id": row["parent_id"],
            "metadata": row["metadata"] or "{}",
            "status": row["status"],
            "error": row["error"],
            "public_permission": row["public_permission"],
            "permissions": row["permissions"] or "[]",
            "workspace_id": row["workspace_id"],
            "access_at": row["access_at"],
            "deleted_at": row["deleted_at"],
            "history": "[]",  # old history refs were plugin-internal; not portable
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "is_deleted": row["is_deleted"],
            "meta_data": None,
        })
        if row["content_reference"] and row["type"] != "folder":
            object_uid = str(uuid.uuid4())
            storage_object_rows.append({
                "uid": object_uid,
                "provider_connection_id": row["provider_connection_id"],
                "content_reference": row["content_reference"],
                "provider_parent_ref": None,
                "type": row["type"],
                "name": row["name"],
                "content_hash": row["content_hash"],
                "content_type": row["content_type"],
                "size": row["size"],
                "metadata": row["metadata"] or "{}",
                "status": "active",
                "last_seen_at": now,
                "deleted_at": None,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "is_deleted": False,
                "meta_data": None,
            })
            link_rows.append({
                "uid": str(uuid.uuid4()),
                "media_file_id": row["uid"],
                "storage_object_id": object_uid,
                "role": "primary",
                "created_at": now,
                "updated_at": now,
                "is_deleted": False,
                "meta_data": None,
            })

    if media_file_rows:
        bind.execute(
            sa.text(
                "INSERT INTO media_files (uid, owner_id, type, name, "
                "parent_id, metadata, status, error, public_permission, "
                "permissions, workspace_id, access_at, deleted_at, history, "
                "created_at, updated_at, is_deleted, meta_data) VALUES "
                "(:uid, :owner_id, :type, :name, :parent_id, :metadata, "
                ":status, :error, :public_permission, :permissions, "
                ":workspace_id, :access_at, :deleted_at, :history, "
                ":created_at, :updated_at, :is_deleted, :meta_data)",
            ),
            media_file_rows,
        )
    if storage_object_rows:
        bind.execute(
            sa.text(
                "INSERT INTO storage_objects (uid, provider_connection_id, "
                "content_reference, provider_parent_ref, type, name, "
                "content_hash, content_type, size, metadata, status, "
                "last_seen_at, deleted_at, created_at, updated_at, "
                "is_deleted, meta_data) VALUES (:uid, "
                ":provider_connection_id, :content_reference, "
                ":provider_parent_ref, :type, :name, :content_hash, "
                ":content_type, :size, :metadata, :status, :last_seen_at, "
                ":deleted_at, :created_at, :updated_at, :is_deleted, "
                ":meta_data)",
            ),
            storage_object_rows,
        )
    if link_rows:
        bind.execute(
            sa.text(
                "INSERT INTO media_file_objects (uid, media_file_id, "
                "storage_object_id, role, created_at, updated_at, "
                "is_deleted, meta_data) VALUES (:uid, :media_file_id, "
                ":storage_object_id, :role, :created_at, :updated_at, "
                ":is_deleted, :meta_data)",
            ),
            link_rows,
        )


def downgrade() -> None:
    op.drop_table("media_file_objects")
    op.drop_table("media_files")
    op.drop_table("storage_objects")
    op.drop_column("provider_connections", "mirror_structure")
    op.drop_column("provider_connections", "import_existing")
