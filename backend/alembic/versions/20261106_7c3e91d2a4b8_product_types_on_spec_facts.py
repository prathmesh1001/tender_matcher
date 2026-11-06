"""product types on spec facts

Revision ID: 7c3e91d2a4b8
Revises: f2291c6a4c65
Create Date: 2026-11-06 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7c3e91d2a4b8"
down_revision: str | Sequence[str] | None = "f2291c6a4c65"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "spec_facts",
        sa.Column(
            "product_type",
            sa.String(length=64),
            nullable=True,
            comment="Ontology product type of the chunk; NULL = general.",
        ),
    )
    op.add_column("spec_facts", sa.Column("product_sub_type", sa.String(length=64), nullable=True))
    # Stored facts take the product of their chunk, like new facts do.
    op.execute(
        "UPDATE spec_facts SET product_type = chunks.product_type,"
        " product_sub_type = chunks.product_sub_type"
        " FROM chunks WHERE spec_facts.chunk_id = chunks.id"
    )
    op.drop_index("ix_spec_facts_lookup", table_name="spec_facts")
    op.create_index(
        "ix_spec_facts_lookup",
        "spec_facts",
        ["manufacturer", "category", "product_type", "attribute"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_spec_facts_lookup", table_name="spec_facts")
    op.create_index(
        "ix_spec_facts_lookup",
        "spec_facts",
        ["manufacturer", "category", "attribute"],
        unique=False,
    )
    op.drop_column("spec_facts", "product_sub_type")
    op.drop_column("spec_facts", "product_type")
