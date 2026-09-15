"""Bind folders created before placement stored a connection on each folder."""

from alembic import op

revision = "0009_bind_existing_folders"
down_revision = "0008_folder_placement"
branch_labels = None
depends_on = None

_BIND_FROM_CHILD_FILE = """
UPDATE media_files
SET provider_connection_id = (
    SELECT so.provider_connection_id
    FROM media_files AS child
    JOIN media_file_objects AS mfo
        ON mfo.media_file_id = child.uid
        AND mfo.is_deleted = 0
    JOIN storage_objects AS so
        ON so.uid = mfo.storage_object_id
        AND so.is_deleted = 0
    WHERE child.parent_id = media_files.uid
        AND child.is_deleted = 0
        AND child.type != 'folder'
        AND so.provider_connection_id IS NOT NULL
    LIMIT 1
)
WHERE media_files.type = 'folder'
    AND media_files.provider_connection_id IS NULL
    AND media_files.is_deleted = 0
"""

_BIND_FROM_CHILD_FOLDER = """
UPDATE media_files
SET provider_connection_id = (
    SELECT child.provider_connection_id
    FROM media_files AS child
    WHERE child.parent_id = media_files.uid
        AND child.is_deleted = 0
        AND child.provider_connection_id IS NOT NULL
    LIMIT 1
)
WHERE media_files.type = 'folder'
    AND media_files.provider_connection_id IS NULL
    AND media_files.is_deleted = 0
"""

_BIND_FROM_IMPORT_ROOT = """
UPDATE media_files
SET provider_connection_id = json_extract(metadata, '$.import_root')
WHERE media_files.type = 'folder'
    AND media_files.provider_connection_id IS NULL
    AND media_files.is_deleted = 0
    AND json_extract(metadata, '$.import_root') IS NOT NULL
"""

_BIND_FILES_FROM_OBJECT = """
UPDATE media_files
SET provider_connection_id = (
    SELECT so.provider_connection_id
    FROM media_file_objects AS mfo
    JOIN storage_objects AS so
        ON so.uid = mfo.storage_object_id
        AND so.is_deleted = 0
    WHERE mfo.media_file_id = media_files.uid
        AND mfo.is_deleted = 0
        AND so.provider_connection_id IS NOT NULL
    LIMIT 1
)
WHERE media_files.type != 'folder'
    AND media_files.provider_connection_id IS NULL
    AND media_files.is_deleted = 0
"""


def upgrade() -> None:
    op.execute(_BIND_FROM_IMPORT_ROOT)
    op.execute(_BIND_FROM_CHILD_FILE)
    for _ in range(8):
        op.execute(_BIND_FROM_CHILD_FOLDER)
    op.execute(_BIND_FILES_FROM_OBJECT)


def downgrade() -> None:
    pass
