"""Add administrator email."""

import sqlalchemy as sa

from alembic import op

revision = "0003_admin_email"
down_revision = "0002_admin_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the administrator login email."""
    with op.batch_alter_table("admin_settings") as batch_op:
        batch_op.add_column(
            sa.Column(
                "email",
                sa.String(),
                nullable=False,
                server_default="",
            ),
        )
        batch_op.create_unique_constraint(
            "uq_admin_settings_email",
            ["email"],
        )


def downgrade() -> None:
    """Remove the administrator login email."""
    with op.batch_alter_table("admin_settings") as batch_op:
        batch_op.drop_constraint(
            "uq_admin_settings_email",
            type_="unique",
        )
        batch_op.drop_column("email")
