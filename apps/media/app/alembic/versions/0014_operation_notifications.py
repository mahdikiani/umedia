import sqlalchemy as sa

from alembic import op

revision = "0014_operation_notifications"
down_revision = "0013_purge_legacy_staging_folders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operation_notifications",
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("item_name", sa.String(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("source_uid", sa.String(), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column("uid", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("meta_data", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("uid"),
        sa.UniqueConstraint(
            "owner_id",
            "source_type",
            "source_uid",
            name="uq_operation_notification_source",
        ),
    )
    op.create_index(
        "ix_operation_notifications_owner_id",
        "operation_notifications",
        ["owner_id"],
    )
    op.create_index(
        "ix_operation_notifications_uid",
        "operation_notifications",
        ["uid"],
        unique=True,
    )
    op.create_index(
        "ix_operation_notifications_created_at",
        "operation_notifications",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_operation_notifications_created_at",
        table_name="operation_notifications",
    )
    op.drop_index(
        "ix_operation_notifications_uid",
        table_name="operation_notifications",
    )
    op.drop_index(
        "ix_operation_notifications_owner_id",
        table_name="operation_notifications",
    )
    op.drop_table("operation_notifications")
