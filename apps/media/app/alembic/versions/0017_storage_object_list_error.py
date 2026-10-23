"""Remember why a folder could not be listed.

One unlistable folder (OneDrive's Personal Vault: Graph answers
`invalidResourceId`) used to abort every inbound sync walk of its
connection, forever. The walk now records the failure on the folder's
StorageObject, carries on with the rest of the tree, and skips a folder
that keeps answering 404/422 for a while instead of asking every pass.

Three nullable columns (SQLite `ADD COLUMN`, no table rewrite); existing
rows keep NULL, meaning no failure recorded.
"""

import sqlalchemy as sa

from alembic import op

revision = "0017_storage_object_list_error"
down_revision = "0016_media_file_object_join_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("storage_objects", sa.Column("list_error", sa.Text(), nullable=True))
    op.add_column(
        "storage_objects",
        sa.Column("list_error_status", sa.Integer(), nullable=True),
    )
    op.add_column(
        "storage_objects",
        sa.Column("list_error_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    # SQLite needs the batch copy-and-move to drop columns.
    with op.batch_alter_table("storage_objects") as batch:
        batch.drop_column("list_error_at")
        batch.drop_column("list_error_status")
        batch.drop_column("list_error")
