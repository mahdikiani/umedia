import sqlalchemy as sa

from alembic import op

revision = "0012_temporary_items"
down_revision = "0011_library_transfers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_file_temporary_items",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("media_file_id", sa.String(), nullable=False),
        sa.Column("uid", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("meta_data", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("uid"),
        sa.UniqueConstraint(
            "user_id",
            "media_file_id",
            name="uq_media_file_temporary_items_user_file",
        ),
    )
    for column in ("user_id", "media_file_id", "uid", "created_at"):
        op.create_index(
            f"ix_media_file_temporary_items_{column}",
            "media_file_temporary_items",
            [column],
            unique=column == "uid",
        )


def downgrade() -> None:
    for column in ("created_at", "uid", "media_file_id", "user_id"):
        op.drop_index(
            f"ix_media_file_temporary_items_{column}",
            table_name="media_file_temporary_items",
        )
    op.drop_table("media_file_temporary_items")
