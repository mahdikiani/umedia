"""Add the resource ACL columns: owner_id, permissions, workspace_id.

Backfill note (existing installs): user accounts live in usso.lite's own
tables -- a *separate* SQLite database from this one -- so this migration
cannot `UPDATE resources SET owner_id = (SELECT ... FROM users)`; there is
no users table here to select from. Pre-ACL rows therefore get the
`legacy-unassigned` sentinel, which matches no real user uid: such rows
stay invisible to everyone (including administrators -- there is no admin
override in the ACL) until an operator re-assigns them, e.g.

    UPDATE resources SET owner_id = '<admin uid>'
    WHERE owner_id = 'legacy-unassigned';

with the uid taken from `GET /users`. Fresh installs never see the
sentinel: `ResourceService.create()` always sets a real owner, and the
model column has no default, so nothing can be created unowned.

The `server_default` is deliberately left in place afterwards rather than
dropped: removing it needs SQLite's copy-and-move table dance (batch
mode) for zero benefit -- the application layer always provides
`owner_id` explicitly.
"""

import sqlalchemy as sa

from alembic import op

revision = "0004_resource_acl"
down_revision = "0003_provider_connections_enabled"
branch_labels = None
depends_on = None

LEGACY_OWNER_SENTINEL = "legacy-unassigned"


def upgrade() -> None:
    op.add_column(
        "resources",
        sa.Column(
            "owner_id",
            sa.String(),
            nullable=False,
            server_default=LEGACY_OWNER_SENTINEL,
        ),
    )
    op.add_column(
        "resources",
        sa.Column("permissions", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "resources",
        sa.Column("workspace_id", sa.String(), nullable=True),
    )
    op.create_index("ix_resources_owner_id", "resources", ["owner_id"])
    op.create_index("ix_resources_workspace_id", "resources", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_resources_workspace_id", table_name="resources")
    op.drop_index("ix_resources_owner_id", table_name="resources")
    op.drop_column("resources", "workspace_id")
    op.drop_column("resources", "permissions")
    op.drop_column("resources", "owner_id")
