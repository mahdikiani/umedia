"""Remember files whose content cannot be read for hashing.

32 files on the `mobin` SFTP connection (links rclone cannot `cat`) failed
the content-hash backfill on every pass. The in-process backoff hid the
repeats but forgot them on each restart, so every restart sent 32 new
error events (GlitchTip UMEDIA-2). A 404/422 answer is now stored on the
StorageObject; the backfill skips the file until a sync sees it change or
the record ages out.

Two nullable columns (SQLite `ADD COLUMN`, no table rewrite); existing
rows keep NULL, meaning no failure recorded.
"""

import sqlalchemy as sa

from alembic import op

revision = "0018_storage_object_hash_error"
down_revision = "0017_storage_object_list_error"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("storage_objects", sa.Column("hash_error", sa.Text(), nullable=True))
    op.add_column(
        "storage_objects",
        sa.Column("hash_error_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    # SQLite needs the batch copy-and-move to drop columns.
    with op.batch_alter_table("storage_objects") as batch:
        batch.drop_column("hash_error_at")
        batch.drop_column("hash_error")
