"""stored files in postgres

Revision ID: 4c1a0f7eda89
Revises: 7c3e91d2a4b8
Create Date: 2026-11-13 10:08:05.741725
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4c1a0f7eda89"
down_revision: str | Sequence[str] | None = "7c3e91d2a4b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema: source files kept in Postgres (FILE_STORE_BACKEND=postgres)."""
    op.create_table(
        "file_blobs",
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False, comment="Bytes."),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("sha256", name=op.f("pk_file_blobs")),
    )
    op.create_table(
        "stored_files",
        sa.Column("ref", sa.Text(), nullable=False, comment="FileStore reference."),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["sha256"],
            ["file_blobs.sha256"],
            name=op.f("fk_stored_files_sha256_file_blobs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("ref", name=op.f("pk_stored_files")),
    )
    op.create_index(op.f("ix_stored_files_sha256"), "stored_files", ["sha256"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_stored_files_sha256"), table_name="stored_files")
    op.drop_table("stored_files")
    op.drop_table("file_blobs")
