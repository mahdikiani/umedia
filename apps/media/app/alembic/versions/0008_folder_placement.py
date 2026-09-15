import sqlalchemy as sa

from alembic import op

revision = "0008_folder_placement"
down_revision = "0007_user_access_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "media_files",
        sa.Column("provider_connection_id", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_media_files_provider_connection_id",
        "media_files",
        ["provider_connection_id"],
    )
    op.create_table(
        "instance_settings",
        sa.Column("placement_policy", sa.String(), nullable=False),
        sa.Column("default_connection_id", sa.String(), nullable=True),
        sa.Column("fill_order", sa.JSON(), nullable=True),
        sa.Column("uid", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("meta_data", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("uid"),
    )
    op.create_index("ix_instance_settings_uid", "instance_settings", ["uid"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_instance_settings_uid", table_name="instance_settings")
    op.drop_table("instance_settings")
    op.drop_index("ix_media_files_provider_connection_id", table_name="media_files")
    op.drop_column("media_files", "provider_connection_id")
