"""Record which algorithm a StorageObject's content_hash is.

Providers report their own hashes (md5, sha1, dropbox, quickxor, ...) in
listings, so the index can be fingerprinted without downloading files.
Existing hashes were all computed by UMedia (SHA-256).
"""

import sqlalchemy as sa

from alembic import op

revision = "0017_storage_object_hash_algorithm"
down_revision = "0016_media_file_object_join_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("storage_objects") as batch:
        batch.add_column(sa.Column("hash_algorithm", sa.String(), nullable=True))
    op.execute(
        "UPDATE storage_objects SET hash_algorithm = 'sha256' "
        "WHERE content_hash IS NOT NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table("storage_objects") as batch:
        batch.drop_column("hash_algorithm")
