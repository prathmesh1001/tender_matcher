"""run owned line items and requirements report json

Revision ID: 248997b9fbb1
Revises: 796bf6ccc66a
Create Date: 2026-11-01 22:15:28.310587
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "248997b9fbb1"
down_revision: str | Sequence[str] | None = "796bf6ccc66a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema. Nothing wrote requirements or results before this; NOT NULL fails if so."""
    op.create_table(
        "line_items",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, comment="Order in the run."),
        sa.Column(
            "item_no", sa.Text(), nullable=False, comment="As printed; can repeat within a tender."
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description_lines", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("description_header", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Text(), nullable=True),
        sa.Column("unit", sa.Text(), nullable=True),
        sa.Column(
            "document", sa.Text(), nullable=False, comment="Tender file the item comes from."
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column(
            "category", sa.String(length=64), nullable=True, comment="Ontology category used."
        ),
        sa.Column("product_type", sa.String(length=128), nullable=True),
        sa.Column("sub_type", sa.String(length=128), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_line_items_run_id_runs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_line_items")),
        sa.UniqueConstraint("run_id", "position", name=op.f("uq_line_items_run_id_position")),
    )
    op.add_column(
        "evidence",
        sa.Column("label", sa.Text(), nullable=False, comment="The field's label as read."),
    )
    op.add_column(
        "evidence",
        sa.Column("value", sa.Text(), nullable=False, comment="The field's value as printed."),
    )
    op.add_column(
        "evidence",
        sa.Column("quote_parts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    )
    op.add_column(
        "evidence",
        sa.Column(
            "status",
            sa.Enum(
                "full_match",
                "partial_match",
                "missing_capability",
                "unsupported",
                "not_applicable",
                name="matchstatus",
                native_enum=False,
                length=32,
            ),
            nullable=True,
            comment="What this field alone decides; NULL if nothing.",
        ),
    )
    op.add_column("evidence", sa.Column("reason", sa.Text(), nullable=False))
    op.alter_column(
        "evidence",
        "verified",
        existing_type=sa.BOOLEAN(),
        nullable=True,
        comment="NULL = not checked yet.",
    )
    op.alter_column(
        "evidence", "match_score", existing_type=sa.DOUBLE_PRECISION(precision=53), nullable=True
    )
    op.add_column("requirements", sa.Column("run_id", sa.Uuid(), nullable=False))
    op.add_column(
        "requirements",
        sa.Column(
            "line_item_id",
            sa.Uuid(),
            nullable=True,
            comment="NULL = a general requirement, outside the item schedule.",
        ),
    )
    op.add_column(
        "requirements",
        sa.Column(
            "ref", sa.Text(), nullable=False, comment="The report's requirement id; can repeat."
        ),
    )
    op.add_column(
        "requirements",
        sa.Column(
            "document", sa.Text(), nullable=False, comment="Tender file the line comes from."
        ),
    )
    op.add_column(
        "requirements", sa.Column("label", sa.Text(), nullable=True, comment="Label as printed.")
    )
    op.add_column(
        "requirements",
        sa.Column("category", sa.String(length=64), nullable=True, comment="Section category."),
    )
    op.add_column(
        "requirements",
        sa.Column(
            "evidence",
            sa.Enum(
                "catalogue",
                "vendor_documents",
                "none",
                name="evidencesource",
                native_enum=False,
                length=32,
            ),
            nullable=True,
        ),
    )
    op.add_column("requirements", sa.Column("product_type", sa.String(length=128), nullable=True))
    op.add_column(
        "requirements",
        sa.Column(
            "mandatory", sa.Boolean(), nullable=True, comment="NULL if the line does not say."
        ),
    )
    op.add_column("requirements", sa.Column("named_make", sa.Text(), nullable=True))
    op.add_column("requirements", sa.Column("single_firm", sa.Boolean(), nullable=False))
    op.add_column(
        "requirements",
        sa.Column("acceptable_makes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    )
    op.add_column("requirements", sa.Column("or_equivalent", sa.Boolean(), nullable=False))
    op.add_column(
        "requirements", sa.Column("flags", postgresql.JSONB(astext_type=sa.Text()), nullable=False)
    )
    op.add_column(
        "requirements",
        sa.Column(
            "kind",
            sa.Enum("numeric", "code", "text", name="valuekind", native_enum=False, length=32),
            nullable=False,
        ),
    )
    op.add_column("requirements", sa.Column("canonical_unit", sa.String(length=64), nullable=True))
    op.add_column(
        "requirements",
        sa.Column("alternatives", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    )
    op.add_column("requirements", sa.Column("any_alternative", sa.Boolean(), nullable=False))
    op.alter_column(
        "requirements",
        "position",
        existing_type=sa.INTEGER(),
        comment="Order in the run.",
        existing_comment="Order in the tender.",
        existing_nullable=False,
    )
    op.alter_column(
        "requirements",
        "unit",
        existing_type=sa.VARCHAR(length=32),
        type_=sa.Text(),
        existing_nullable=True,
    )
    op.drop_index(op.f("ix_requirements_tender_position"), table_name="requirements")
    op.create_index(
        op.f("ix_requirements_line_item_id"), "requirements", ["line_item_id"], unique=False
    )
    op.create_index(
        "ix_requirements_run_position", "requirements", ["run_id", "position"], unique=False
    )
    op.drop_constraint(
        op.f("fk_requirements_tender_id_tenders"), "requirements", type_="foreignkey"
    )
    op.create_foreign_key(
        op.f("fk_requirements_run_id_runs"),
        "requirements",
        "runs",
        ["run_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("fk_requirements_line_item_id_line_items"),
        "requirements",
        "line_items",
        ["line_item_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_column("requirements", "tender_id")
    op.add_column(
        "results",
        sa.Column(
            "catalogue", sa.Text(), nullable=True, comment="Catalogue file this row is from."
        ),
    )
    op.add_column("results", sa.Column("tied", sa.Boolean(), nullable=False))
    op.add_column(
        "results",
        sa.Column(
            "basis",
            sa.Enum(
                "attribute", "label_words", "none", name="labelbasis", native_enum=False, length=32
            ),
            nullable=False,
        ),
    )
    op.alter_column("results", "manufacturer", existing_type=sa.VARCHAR(length=128), nullable=True)
    op.alter_column(
        "results",
        "confidence",
        existing_type=sa.DOUBLE_PRECISION(precision=53),
        nullable=True,
        comment="NULL = not computed yet.",
    )
    op.drop_constraint(
        op.f("uq_results_run_id_requirement_id_manufacturer"), "results", type_="unique"
    )
    op.create_unique_constraint(
        op.f("uq_results_run_id_requirement_id_manufacturer_catalogue"),
        "results",
        ["run_id", "requirement_id", "manufacturer", "catalogue"],
        postgresql_nulls_not_distinct=True,
    )
    op.add_column(
        "runs",
        sa.Column(
            "report",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="The full report as produced, so a past run reopens exactly as it was.",
        ),
    )


def downgrade() -> None:
    """Downgrade schema. Removes requirements and results: the old shape cannot hold them."""
    op.execute("DELETE FROM requirements")
    op.drop_column("runs", "report")
    op.drop_constraint(
        op.f("uq_results_run_id_requirement_id_manufacturer_catalogue"), "results", type_="unique"
    )
    op.create_unique_constraint(
        op.f("uq_results_run_id_requirement_id_manufacturer"),
        "results",
        ["run_id", "requirement_id", "manufacturer"],
        postgresql_nulls_not_distinct=False,
    )
    op.alter_column(
        "results",
        "confidence",
        existing_type=sa.DOUBLE_PRECISION(precision=53),
        nullable=False,
        comment=None,
        existing_comment="NULL = not computed yet.",
    )
    op.alter_column("results", "manufacturer", existing_type=sa.VARCHAR(length=128), nullable=False)
    op.drop_column("results", "basis")
    op.drop_column("results", "tied")
    op.drop_column("results", "catalogue")
    op.add_column(
        "requirements", sa.Column("tender_id", sa.UUID(), autoincrement=False, nullable=False)
    )
    op.drop_constraint(
        op.f("fk_requirements_line_item_id_line_items"), "requirements", type_="foreignkey"
    )
    op.drop_constraint(op.f("fk_requirements_run_id_runs"), "requirements", type_="foreignkey")
    op.create_foreign_key(
        op.f("fk_requirements_tender_id_tenders"),
        "requirements",
        "tenders",
        ["tender_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_index("ix_requirements_run_position", table_name="requirements")
    op.drop_index(op.f("ix_requirements_line_item_id"), table_name="requirements")
    op.create_index(
        op.f("ix_requirements_tender_position"),
        "requirements",
        ["tender_id", "position"],
        unique=False,
    )
    op.alter_column(
        "requirements",
        "unit",
        existing_type=sa.Text(),
        type_=sa.VARCHAR(length=32),
        existing_nullable=True,
    )
    op.alter_column(
        "requirements",
        "position",
        existing_type=sa.INTEGER(),
        comment="Order in the tender.",
        existing_comment="Order in the run.",
        existing_nullable=False,
    )
    op.drop_column("requirements", "any_alternative")
    op.drop_column("requirements", "alternatives")
    op.drop_column("requirements", "canonical_unit")
    op.drop_column("requirements", "kind")
    op.drop_column("requirements", "flags")
    op.drop_column("requirements", "or_equivalent")
    op.drop_column("requirements", "acceptable_makes")
    op.drop_column("requirements", "single_firm")
    op.drop_column("requirements", "named_make")
    op.drop_column("requirements", "mandatory")
    op.drop_column("requirements", "product_type")
    op.drop_column("requirements", "evidence")
    op.drop_column("requirements", "category")
    op.drop_column("requirements", "label")
    op.drop_column("requirements", "document")
    op.drop_column("requirements", "ref")
    op.drop_column("requirements", "line_item_id")
    op.drop_column("requirements", "run_id")
    op.alter_column(
        "evidence", "match_score", existing_type=sa.DOUBLE_PRECISION(precision=53), nullable=False
    )
    op.alter_column(
        "evidence",
        "verified",
        existing_type=sa.BOOLEAN(),
        nullable=False,
        comment=None,
        existing_comment="NULL = not checked yet.",
    )
    op.drop_column("evidence", "reason")
    op.drop_column("evidence", "status")
    op.drop_column("evidence", "quote_parts")
    op.drop_column("evidence", "value")
    op.drop_column("evidence", "label")
    op.drop_table("line_items")
