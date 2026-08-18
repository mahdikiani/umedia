"""Add administrator settings."""

import sqlalchemy as sa

from alembic import op

revision = "0002_admin_settings"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create administrator authentication state."""
    op.create_table(
        "admin_settings",
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("password_version", sa.Integer(), nullable=False),
        sa.Column("uid", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("meta_data", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("uid"),
    )
    op.create_index(
        op.f("ix_admin_settings_created_at"),
        "admin_settings",
        ["created_at"],
    )
    op.create_index(
        op.f("ix_admin_settings_uid"),
        "admin_settings",
        ["uid"],
        unique=True,
    )


def downgrade() -> None:
    """Drop administrator authentication state."""
    op.drop_index(op.f("ix_admin_settings_uid"), table_name="admin_settings")
    op.drop_index(
        op.f("ix_admin_settings_created_at"),
        table_name="admin_settings",
    )
    op.drop_table("admin_settings")
