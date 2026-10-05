"""Composite index for the MediaFile -> primary StorageObject join.

`_joined_select` joins on (media_file_id, role, is_deleted). With only
single-column indexes, SQLite chose `ix_media_file_objects_role` -- every
row is "primary", so it scanned all links per file. On the preview
library (57,828 files, 71,250 links) loading the visible tree took over
two minutes; with this index it takes 0.2 s.
"""

from alembic import op

revision = "0016_media_file_object_join_index"
down_revision = "0015_bucket_safe_connection_names"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_media_file_objects_file_role_live",
        "media_file_objects",
        ["media_file_id", "role", "is_deleted"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_media_file_objects_file_role_live",
        table_name="media_file_objects",
    )
