"""Pydantic v2 request / response models.

These are the API contract. ORM objects are never returned directly; every
response goes through a schema so that internal columns (file paths, raw
provider payloads) are only exposed where we intend them to be.
"""

from __future__ import annotations

import uuid
from datetime import date as date_type
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.utils.constants import BillStatus, MatchType, ModelProvider, SyncStatus

# --------------------------------------------------------------------------
# Shared
# --------------------------------------------------------------------------


class ORMModel(BaseModel):
    """Base for schemas populated straight from a SQLAlchemy row."""

    model_config = ConfigDict(from_attributes=True, protected_namespaces=())


class ErrorResponse(BaseModel):
    """Uniform error envelope used by the exception handlers."""

    detail: str
    error_type: str = "error"
    context: dict[str, Any] | None = None


# --------------------------------------------------------------------------
# Bills
# --------------------------------------------------------------------------


class BillUploadResponse(ORMModel):
    """Returned by ``POST /api/bills/upload``."""

    id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    status: BillStatus
    uploaded_at: datetime


class BillSummary(ORMModel):
    """One row of the dashboard table."""

    id: uuid.UUID
    filename: str
    status: BillStatus
    uploaded_at: datetime
    extraction_count: int = 0
    has_ground_truth: bool = False


class BillDetail(ORMModel):
    """Full bill payload including every extraction attempt."""

    id: uuid.UUID
    filename: str
    status: BillStatus
    uploaded_at: datetime
    content_type: str
    size_bytes: int
    error_message: str | None = None
    image_url: str
    extraction_results: list["ExtractionResponse"] = Field(default_factory=list)
    ground_truth: "GroundTruthResponse | None" = None


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------


class ExtractionRequest(BaseModel):
    """Body of ``POST /api/extract/{bill_id}``."""

    model_config = ConfigDict(protected_namespaces=())

    models: Annotated[list[ModelProvider], Field(min_length=1)] = Field(
        default_factory=lambda: [ModelProvider.GEMINI, ModelProvider.CLAUDE],
        description="Provider slugs to run. Unconfigured providers are rejected up front.",
        examples=[["gemini", "claude", "openai"]],
    )

    @field_validator("models")
    @classmethod
    def _dedupe(cls, value: list[ModelProvider]) -> list[ModelProvider]:
        """Preserve order but drop duplicates -- running a model twice in one
        request would double the bill for no extra signal."""
        seen: set[ModelProvider] = set()
        out: list[ModelProvider] = []
        for item in value:
            if item not in seen:
                seen.add(item)
                out.append(item)
        return out


class ExtractedFields(BaseModel):
    """The six fields, exactly as parsed out of the model's JSON."""

    vendor_name: str | None = None
    bill_number: str | None = None
    date: date_type | None = None
    amount: Decimal | None = None
    currency: str = "INR"
    tax_gst_details: str | None = None


class ExtractionResponse(ORMModel):
    """One model's result row, with cost and latency telemetry attached."""

    id: uuid.UUID
    bill_id: uuid.UUID
    model_name: str
    provider: str
    vendor_name: str | None = None
    bill_number: str | None = None
    date: date_type | None = None
    amount: Decimal | None = None
    currency: str = "INR"
    tax_gst_details: str | None = None
    raw_response: str = ""
    latency_ms: int = 0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    token_source: str = "estimated"
    succeeded: bool = True
    error_message: str | None = None
    created_at: datetime


class ExtractionRunResponse(BaseModel):
    """Summary returned after running one or more models over a bill."""

    bill_id: uuid.UUID
    status: BillStatus
    requested_models: list[str]
    succeeded_models: list[str]
    failed_models: list[str]
    results: list[ExtractionResponse]


# --------------------------------------------------------------------------
# Ground truth
# --------------------------------------------------------------------------


class GroundTruthCreate(BaseModel):
    """Body of ``POST /api/ground-truth/{bill_id}``."""

    vendor_name: Annotated[str, Field(min_length=1, max_length=512)]
    bill_number: str | None = None
    date: date_type | None = None
    amount: Decimal
    currency: str = "INR"
    tax_gst_details: str | None = None

    @field_validator("currency")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.strip().upper() or "INR"


class GroundTruthResponse(ORMModel):
    """Stored answer key."""

    id: uuid.UUID
    bill_id: uuid.UUID
    vendor_name: str
    bill_number: str | None = None
    date: date_type | None = None
    amount: Decimal
    currency: str = "INR"
    tax_gst_details: str | None = None
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------


class FieldScore(BaseModel):
    """Score for a single field of a single extraction."""

    field_name: str
    score: float
    match_type: MatchType
    predicted_value: str | None = None
    expected_value: str | None = None
    notes: str | None = None


class ExtractionEvaluation(BaseModel):
    """All six field scores for one extraction result."""

    model_config = ConfigDict(protected_namespaces=())

    extraction_result_id: uuid.UUID
    model_name: str
    overall_accuracy: float
    fields: dict[str, FieldScore]
    cost_usd: float
    latency_ms: int


class BillEvaluationResponse(BaseModel):
    """Result of evaluating every extraction attached to one bill."""

    bill_id: uuid.UUID
    evaluations: list[ExtractionEvaluation]


class FieldAccuracy(BaseModel):
    """Aggregated accuracy for one field across many bills."""

    accuracy: float
    match_type: MatchType
    sample_size: int
    match_type_counts: dict[str, int] = Field(default_factory=dict)


class EvaluationReport(BaseModel):
    """Per-model aggregate, matching the shape required by the brief."""

    model_config = ConfigDict(protected_namespaces=())

    model_name: str
    provider: str
    bills_evaluated: int
    overall_accuracy: float
    fields: dict[str, FieldAccuracy]
    total_cost_usd: float
    avg_cost_per_bill_usd: float
    cost_per_100_bills_usd: float
    avg_latency_ms: float
    success_rate: float


class AggregateReportResponse(BaseModel):
    """The full leaderboard plus a plain-English recommendation."""

    generated_at: datetime
    bills_with_ground_truth: int
    reports: list[EvaluationReport]
    recommendation: str | None = None


# --------------------------------------------------------------------------
# Zoho
# --------------------------------------------------------------------------


class ZohoExpenseCreate(BaseModel):
    """Body of ``POST /api/zoho/expenses``."""

    extraction_result_id: uuid.UUID
    account_name: str | None = Field(
        default=None, description="Zoho chart-of-accounts name. Falls back to the configured default."
    )
    description: str | None = None


class ZohoExpenseResponse(ORMModel):
    """Outcome of a push to Zoho Books."""

    id: uuid.UUID
    extraction_result_id: uuid.UUID
    zoho_expense_id: str | None = None
    sync_status: SyncStatus
    error_message: str | None = None
    created_at: datetime


class ZohoAuthUrlResponse(BaseModel):
    """Step 1 of the OAuth dance."""

    authorization_url: str
    scope: str
    instructions: str


class ZohoCallbackResponse(BaseModel):
    """Step 2 -- the refresh token the user must paste into ``.env``."""

    refresh_token: str | None = None
    api_domain: str | None = None
    message: str


class ZohoStatusResponse(BaseModel):
    """Whether the backend currently holds usable Zoho credentials."""

    configured: bool
    organization_id: str | None = None
    books_base_url: str
    missing: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------


class HealthResponse(BaseModel):
    """``GET /api/health``."""

    status: str
    database: str
    configured_providers: list[str]
    models: dict[str, str]
    zoho_configured: bool


BillDetail.model_rebuild()
