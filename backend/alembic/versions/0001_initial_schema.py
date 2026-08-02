"""Initial schema: bills, extractions, ground truth, scores, Zoho mappings.

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-01
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Enums are stored as VARCHAR with an application-side check rather than as
# native PostgreSQL ENUM types. Native enums need an ALTER TYPE (and a lock) to
# add a value; a VARCHAR needs nothing. For a status column that will grow new
# states, that trade is worth the lost database-level constraint.
_BILL_STATUS = sa.Enum(
    "uploaded", "processing", "completed", "failed",
    name="bill_status", native_enum=False, length=32,
)
_MATCH_TYPE = sa.Enum(
    "exact", "fuzzy", "partial", "missing", "not_applicable",
    name="match_type", native_enum=False, length=32,
)
_SYNC_STATUS = sa.Enum(
    "pending", "synced", "failed",
    name="sync_status", native_enum=False, length=32,
)


def upgrade() -> None:
    """Create every table, index and constraint."""
    op.create_table(
        "bills",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("original_path", sa.String(length=1024), nullable=False),
        sa.Column("content_type", sa.String(length=64), nullable=False, server_default="image/jpeg"),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("status", _BILL_STATUS, nullable=False, server_default="uploaded"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bills_status", "bills", ["status"])

    op.create_table(
        "extraction_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bill_id", sa.Uuid(), nullable=False),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("vendor_name", sa.String(length=512), nullable=True),
        sa.Column("bill_number", sa.String(length=128), nullable=True),
        sa.Column("date", sa.Date(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="INR"),
        sa.Column("tax_gst_details", sa.Text(), nullable=True),
        sa.Column("raw_response", sa.Text(), nullable=False, server_default=""),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("token_source", sa.String(length=16), nullable=False, server_default="estimated"),
        sa.Column("succeeded", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["bill_id"], ["bills.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_extraction_results_bill_id", "extraction_results", ["bill_id"])
    op.create_index("ix_extraction_bill_model", "extraction_results", ["bill_id", "model_name"])

    op.create_table(
        "ground_truths",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bill_id", sa.Uuid(), nullable=False),
        sa.Column("vendor_name", sa.String(length=512), nullable=False),
        sa.Column("bill_number", sa.String(length=128), nullable=True),
        sa.Column("date", sa.Date(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="INR"),
        sa.Column("tax_gst_details", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["bill_id"], ["bills.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bill_id", name="uq_ground_truth_bill"),
    )

    op.create_table(
        "evaluation_scores",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("extraction_result_id", sa.Uuid(), nullable=False),
        sa.Column("field_name", sa.String(length=64), nullable=False),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("match_type", _MATCH_TYPE, nullable=False),
        sa.Column("predicted_value", sa.Text(), nullable=True),
        sa.Column("expected_value", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["extraction_result_id"], ["extraction_results.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("extraction_result_id", "field_name", name="uq_score_result_field"),
    )
    op.create_index(
        "ix_evaluation_scores_extraction_result_id", "evaluation_scores", ["extraction_result_id"]
    )

    op.create_table(
        "zoho_expense_mappings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("extraction_result_id", sa.Uuid(), nullable=False),
        sa.Column("zoho_expense_id", sa.String(length=128), nullable=True),
        sa.Column("sync_status", _SYNC_STATUS, nullable=False, server_default="pending"),
        sa.Column("request_payload", sa.Text(), nullable=True),
        sa.Column("response_payload", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["extraction_result_id"], ["extraction_results.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("extraction_result_id", name="uq_zoho_mapping_extraction"),
    )


def downgrade() -> None:
    """Drop everything in reverse dependency order."""
    op.drop_table("zoho_expense_mappings")
    op.drop_index("ix_evaluation_scores_extraction_result_id", table_name="evaluation_scores")
    op.drop_table("evaluation_scores")
    op.drop_table("ground_truths")
    op.drop_index("ix_extraction_bill_model", table_name="extraction_results")
    op.drop_index("ix_extraction_results_bill_id", table_name="extraction_results")
    op.drop_table("extraction_results")
    op.drop_index("ix_bills_status", table_name="bills")
    op.drop_table("bills")
