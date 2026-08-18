"""Add resources table."""

import sqlalchemy as sa

from alembic import op

revision = "0002_resources"
down_revision = "0001_provider_connections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "resources",
        sa.Column("provider_connection_id", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("parent_id", sa.String(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("content_reference", sa.String(), nullable=True),
        sa.Column("content_hash", sa.String(), nullable=True),
        sa.Column("content_type", sa.String(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("public_permission", sa.String(), nullable=False),
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
        "provider_connection_id",
        "type",
        "name",
        "parent_id",
        "content_hash",
        "status",
        "uid",
        "created_at",
    ):
        op.create_index(
            f"ix_resources_{column}",
            "resources",
            [column],
            unique=column == "uid",
        )


def downgrade() -> None:
    op.drop_table("resources")
