"""Add provider_connections.enabled."""

import sqlalchemy as sa

from alembic import op

revision = "0003_provider_connections_enabled"
down_revision = "0002_resources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "provider_connections",
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default=sa.true(),
        ),
    )
    op.create_index(
        "ix_provider_connections_enabled",
        "provider_connections",
        ["enabled"],
    )


def downgrade() -> None:
    op.drop_index("ix_provider_connections_enabled", table_name="provider_connections")
    op.drop_column("provider_connections", "enabled")
