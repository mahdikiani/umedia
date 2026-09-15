"""Soft-delete legacy Temporary staging folders (metadata.staging).

Temporary is now a per-user pointer clipboard (`media_file_temporary_items`),
not a root MediaFile folder.
"""

from datetime import datetime

import sqlalchemy as sa

from alembic import op

revision = "0013_purge_legacy_staging_folders"
down_revision = "0012_temporary_items"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    roots = conn.execute(
        sa.text(
            """
            SELECT uid FROM media_files
            WHERE is_deleted = 0
              AND type = 'folder'
              AND json_extract(metadata, '$.staging') = true
            """,
        ),
    ).fetchall()
    if not roots:
        return
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    for (root_uid,) in roots:
        conn.execute(
            sa.text(
                """
                WITH RECURSIVE tree(uid) AS (
                    SELECT :root_uid
                    UNION ALL
                    SELECT m.uid
                    FROM media_files m
                    JOIN tree t ON m.parent_id = t.uid
                    WHERE m.is_deleted = 0
                )
                UPDATE media_files
                SET is_deleted = 1, deleted_at = :now
                WHERE uid IN (SELECT uid FROM tree)
                """,
            ),
            {"root_uid": root_uid, "now": now},
        )


def downgrade() -> None:
    # Irreversible: restored staging folders would contradict pointer clipboard.
    pass
