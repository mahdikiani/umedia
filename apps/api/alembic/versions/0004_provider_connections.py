"""Add encrypted provider connections."""

import sqlalchemy as sa

from alembic import op

revision = "0004_provider_connections"
down_revision = "0003_admin_email"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_connections",
        sa.Column("provider_type", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("encrypted_config", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("last_tested_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("uid", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("meta_data", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("uid"),
    )
    for column in ("provider_type", "name", "status", "uid", "created_at"):
        op.create_index(
            f"ix_provider_connections_{column}",
            "provider_connections",
            [column],
            unique=column == "uid",
        )


def downgrade() -> None:
    op.drop_table("provider_connections")
