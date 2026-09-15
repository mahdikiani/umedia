"""Alembic 0011: library_transfers + allow shared StorageObject links.

1. Create `library_transfers` for bulk move/copy jobs.
2. Drop `uq_media_file_objects_storage_object` so a same-storage library
   copy can share one StorageObject across MediaFiles (second link).
"""

import sqlalchemy as sa

from alembic import op

revision = "0011_library_transfers"
down_revision = "0010_provider_connection_owner"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "library_transfers",
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("source_ids", sa.JSON(), nullable=False),
        sa.Column("dest_parent_id", sa.String(), nullable=True),
        sa.Column("conflict", sa.String(), nullable=False),
        sa.Column("total_items", sa.Integer(), nullable=False),
        sa.Column("done_items", sa.Integer(), nullable=False),
        sa.Column("failed_items", sa.Integer(), nullable=False),
        sa.Column("current_name", sa.String(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("uid", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("meta_data", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("uid"),
    )
    op.create_index(
        "ix_library_transfers_owner_id",
        "library_transfers",
        ["owner_id"],
    )
    op.create_index(
        "ix_library_transfers_uid",
        "library_transfers",
        ["uid"],
        unique=True,
    )
    op.create_index(
        "ix_library_transfers_status",
        "library_transfers",
        ["status"],
    )
    op.create_index(
        "ix_library_transfers_created_at",
        "library_transfers",
        ["created_at"],
    )

    # SQLite cannot DROP CONSTRAINT in place; batch mode recreates the table.
    with op.batch_alter_table("media_file_objects") as batch_op:
        batch_op.drop_constraint(
            "uq_media_file_objects_storage_object",
            type_="unique",
        )


def downgrade() -> None:
    with op.batch_alter_table("media_file_objects") as batch_op:
        batch_op.create_unique_constraint(
            "uq_media_file_objects_storage_object",
            ["storage_object_id"],
        )
    op.drop_index("ix_library_transfers_created_at", table_name="library_transfers")
    op.drop_index("ix_library_transfers_status", table_name="library_transfers")
    op.drop_index("ix_library_transfers_uid", table_name="library_transfers")
    op.drop_index("ix_library_transfers_owner_id", table_name="library_transfers")
    op.drop_table("library_transfers")
