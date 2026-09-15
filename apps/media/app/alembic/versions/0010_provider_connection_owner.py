"""Add owner_id to provider_connections (per-user storage connections).

Backfill: prefer the first admin in usso.lite's `localuser` table (same
pattern as migration 0005). If no admin exists, fall back to the first
localuser; only then use `legacy-unassigned` so preview installs that
already have an admin keep working after upgrade.
"""

import json

import sqlalchemy as sa

from alembic import op

revision = "0010_provider_connection_owner"
down_revision = "0009_bind_existing_folders"
branch_labels = None
depends_on = None

LEGACY_OWNER_SENTINEL = "legacy-unassigned"


def _first_admin_uid(bind: sa.engine.Connection) -> str | None:
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


def _first_localuser_uid(bind: sa.engine.Connection) -> str | None:
    inspector = sa.inspect(bind)
    if "localuser" not in inspector.get_table_names():
        return None
    row = bind.execute(sa.text(
        "SELECT uid FROM localuser "
        "WHERE is_deleted = 0 ORDER BY created_at LIMIT 1",
    )).fetchone()
    return None if row is None else row[0]


def upgrade() -> None:
    op.add_column(
        "provider_connections",
        sa.Column(
            "owner_id",
            sa.String(),
            nullable=False,
            server_default=LEGACY_OWNER_SENTINEL,
        ),
    )
    op.create_index(
        "ix_provider_connections_owner_id",
        "provider_connections",
        ["owner_id"],
    )

    bind = op.get_bind()
    owner_uid = (
        _first_admin_uid(bind)
        or _first_localuser_uid(bind)
        or LEGACY_OWNER_SENTINEL
    )
    if owner_uid != LEGACY_OWNER_SENTINEL:
        bind.execute(
            sa.text(
                "UPDATE provider_connections "
                "SET owner_id = :owner_id "
                "WHERE owner_id = :sentinel",
            ),
            {"owner_id": owner_uid, "sentinel": LEGACY_OWNER_SENTINEL},
        )


def downgrade() -> None:
    op.drop_index(
        "ix_provider_connections_owner_id",
        table_name="provider_connections",
    )
    op.drop_column("provider_connections", "owner_id")
