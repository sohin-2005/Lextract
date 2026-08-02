"""SQLAlchemy ORM models.

Every primary key is a UUID (``uuid4``) rather than a serial integer: bill IDs
are handed to the frontend and echoed into Zoho Books descriptions, and
sequential IDs would leak volume and invite enumeration.
"""

from __future__ import annotations

import uuid
from datetime import date as date_type
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Date as SADate
from sqlalchemy import Uuid as SAUuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.utils.constants import BillStatus, MatchType, SyncStatus


def _uuid_pk() -> Mapped[uuid.UUID]:
    """Standard UUID primary-key column."""
    return mapped_column(SAUuid(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Bill(Base):
    """A single uploaded bill image."""

    __tablename__ = "bills"

    id: Mapped[uuid.UUID] = _uuid_pk()
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    original_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False, default="image/jpeg")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    status: Mapped[BillStatus] = mapped_column(
        SAEnum(BillStatus, name="bill_status", native_enum=False, length=32),
        nullable=False,
        default=BillStatus.UPLOADED,
        index=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    extraction_results: Mapped[list["ExtractionResult"]] = relationship(
        back_populates="bill",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ExtractionResult.created_at",
    )
    ground_truth: Mapped["GroundTruth | None"] = relationship(
        back_populates="bill", cascade="all, delete-orphan", lazy="selectin", uselist=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Bill {self.id} {self.filename!r} {self.status}>"


class ExtractionResult(Base):
    """One model's attempt at parsing one bill.

    Re-running extraction always INSERTs a new row -- results are an immutable
    audit log, so a later run can never silently rewrite the numbers a reviewer
    already looked at.
    """

    __tablename__ = "extraction_results"
    __table_args__ = (Index("ix_extraction_bill_model", "bill_id", "model_name"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    bill_id: Mapped[uuid.UUID] = mapped_column(
        SAUuid(as_uuid=True), ForeignKey("bills.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)

    vendor_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    bill_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    date: Mapped[date_type | None] = mapped_column(SADate, nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="INR")
    tax_gst_details: Mapped[str | None] = mapped_column(Text, nullable=True)

    raw_response: Mapped[str] = mapped_column(Text, nullable=False, default="")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token_source: Mapped[str] = mapped_column(String(16), nullable=False, default="estimated")

    succeeded: Mapped[bool] = mapped_column(nullable=False, default=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    bill: Mapped[Bill] = relationship(back_populates="extraction_results")
    scores: Mapped[list["EvaluationScore"]] = relationship(
        back_populates="extraction_result", cascade="all, delete-orphan", lazy="selectin"
    )
    zoho_mapping: Mapped["ZohoExpenseMapping | None"] = relationship(
        back_populates="extraction_result",
        cascade="all, delete-orphan",
        lazy="selectin",
        uselist=False,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ExtractionResult {self.model_name} bill={self.bill_id}>"


class GroundTruth(Base):
    """Human-verified answer key for one bill. At most one row per bill."""

    __tablename__ = "ground_truths"
    __table_args__ = (UniqueConstraint("bill_id", name="uq_ground_truth_bill"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    bill_id: Mapped[uuid.UUID] = mapped_column(
        SAUuid(as_uuid=True), ForeignKey("bills.id", ondelete="CASCADE"), nullable=False
    )
    vendor_name: Mapped[str] = mapped_column(String(512), nullable=False)
    bill_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    date: Mapped[date_type | None] = mapped_column(SADate, nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="INR")
    tax_gst_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    bill: Mapped[Bill] = relationship(back_populates="ground_truth")


class EvaluationScore(Base):
    """Score for one field of one extraction result."""

    __tablename__ = "evaluation_scores"
    __table_args__ = (
        UniqueConstraint("extraction_result_id", "field_name", name="uq_score_result_field"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    extraction_result_id: Mapped[uuid.UUID] = mapped_column(
        SAUuid(as_uuid=True),
        ForeignKey("extraction_results.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    field_name: Mapped[str] = mapped_column(String(64), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    match_type: Mapped[MatchType] = mapped_column(
        SAEnum(MatchType, name="match_type", native_enum=False, length=32), nullable=False
    )
    predicted_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    extraction_result: Mapped[ExtractionResult] = relationship(back_populates="scores")


class ZohoExpenseMapping(Base):
    """Links an extraction result to the expense it created in Zoho Books."""

    __tablename__ = "zoho_expense_mappings"
    __table_args__ = (
        UniqueConstraint("extraction_result_id", name="uq_zoho_mapping_extraction"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    extraction_result_id: Mapped[uuid.UUID] = mapped_column(
        SAUuid(as_uuid=True),
        ForeignKey("extraction_results.id", ondelete="CASCADE"),
        nullable=False,
    )
    zoho_expense_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sync_status: Mapped[SyncStatus] = mapped_column(
        SAEnum(SyncStatus, name="sync_status", native_enum=False, length=32),
        nullable=False,
        default=SyncStatus.PENDING,
    )
    request_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    extraction_result: Mapped[ExtractionResult] = relationship(back_populates="zoho_mapping")
