"""gem marketplace discovery

Revision ID: 9a2d6c41e7b3
Revises: 4c1a0f7eda89
Create Date: 2026-11-18 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "9a2d6c41e7b3"
down_revision: str | Sequence[str] | None = "4c1a0f7eda89"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema: auto-sourced documents, re-matched runs and GeM discovery jobs."""
    op.add_column(
        "documents",
        sa.Column(
            "auto_sourced",
            sa.Boolean(),
            server_default="false",
            nullable=False,
            comment="Found by the system (GeM Marketplace), not registered and checked by hand.",
        ),
    )
    op.add_column(
        "runs",
        sa.Column(
            "parent_run_id",
            sa.Uuid(),
            nullable=True,
            comment="The run this one re-matched (GeM listings added); the parent stays unchanged.",
        ),
    )
    op.create_foreign_key(
        op.f("fk_runs_parent_run_id_runs"),
        "runs",
        "runs",
        ["parent_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_table(
        "discovery_jobs",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("product_type", sa.String(length=128), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "listings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="Each listing read: url, brand, model, kept or why not.",
        ),
        sa.Column(
            "result_run_id",
            sa.Uuid(),
            nullable=True,
            comment="The re-matched run with the kept listings.",
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.id"],
            name=op.f("fk_discovery_jobs_run_id_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["result_run_id"],
            ["runs.id"],
            name=op.f("fk_discovery_jobs_result_run_id_runs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_discovery_jobs")),
    )
    op.create_index(op.f("ix_discovery_jobs_run_id"), "discovery_jobs", ["run_id"], unique=False)
    op.create_index(op.f("ix_discovery_jobs_status"), "discovery_jobs", ["status"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_discovery_jobs_status"), table_name="discovery_jobs")
    op.drop_index(op.f("ix_discovery_jobs_run_id"), table_name="discovery_jobs")
    op.drop_table("discovery_jobs")
    op.drop_constraint(op.f("fk_runs_parent_run_id_runs"), "runs", type_="foreignkey")
    op.drop_column("runs", "parent_run_id")
    op.drop_column("documents", "auto_sourced")
