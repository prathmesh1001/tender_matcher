"""withdrawn gem listings

Revision ID: b7e1f3a9c204
Revises: 9a2d6c41e7b3
Create Date: 2026-11-19 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e1f3a9c204"
down_revision: str | Sequence[str] | None = "9a2d6c41e7b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema: a stored GeM listing that fails a later check is withdrawn, not deleted."""
    op.add_column(
        "documents",
        sa.Column(
            "withdrawn_reason",
            sa.Text(),
            nullable=True,
            comment="Why an auto-sourced listing is no longer used (a later check rejected it); "
            "kept so earlier reports still resolve their evidence.",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("documents", "withdrawn_reason")
