"""Per-user S3-style access key pairs (`user_access_keys`).

Temporary share links are now HMAC-signed with a user's own secret
instead of the installation master key. No data backfill: users who
predate this table get a default key lazily, on their first mint
(`UserAccessKeyService.ensure_default_key`) or at next user creation.
"""

import sqlalchemy as sa

from alembic import op

revision = "0007_user_access_keys"
down_revision = "0006_media_file_stars"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_access_keys",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("access_key_id", sa.String(), nullable=False),
        sa.Column("encrypted_secret", sa.Text(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("uid", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("meta_data", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("uid"),
    )
    for column, unique in (
        ("user_id", False),
        ("access_key_id", True),
        ("is_active", False),
        ("uid", True),
        ("created_at", False),
    ):
        op.create_index(
            f"ix_user_access_keys_{column}",
            "user_access_keys",
            [column],
            unique=unique,
        )


def downgrade() -> None:
    for column in ("created_at", "uid", "is_active", "access_key_id", "user_id"):
        op.drop_index(
            f"ix_user_access_keys_{column}",
            table_name="user_access_keys",
        )
    op.drop_table("user_access_keys")
