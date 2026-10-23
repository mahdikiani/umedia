"""One durable record per background job run (sync walk, hash pass).

A 40-minute walk that aborted used to leave a single ERROR line behind:
no start/end, no folders visited, no record of which folder failed. Each
run now gets a row; its uid is the run id carried by the job's logs, its
plugin calls and its GlitchTip events.
"""

import sqlalchemy as sa

from alembic import op

revision = "0019_job_runs"
down_revision = "0018_storage_object_hash_error"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_runs",
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("provider_connection_id", sa.String(), nullable=False),
        sa.Column("trigger", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("uid", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("meta_data", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("uid"),
    )
    op.create_index("ix_job_runs_uid", "job_runs", ["uid"], unique=True)
    op.create_index("ix_job_runs_created_at", "job_runs", ["created_at"])
    op.create_index("ix_job_runs_kind", "job_runs", ["kind"])
    op.create_index(
        "ix_job_runs_provider_connection_id",
        "job_runs",
        ["provider_connection_id"],
    )
    op.create_index("ix_job_runs_status", "job_runs", ["status"])
    op.create_index("ix_job_runs_started_at", "job_runs", ["started_at"])


def downgrade() -> None:
    for name in (
        "ix_job_runs_started_at",
        "ix_job_runs_status",
        "ix_job_runs_provider_connection_id",
        "ix_job_runs_kind",
        "ix_job_runs_created_at",
        "ix_job_runs_uid",
    ):
        op.drop_index(name, table_name="job_runs")
    op.drop_table("job_runs")
