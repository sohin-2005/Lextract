"""Orchestrates extraction: image in, persisted per-model results out."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date as date_type
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Bill, ExtractionResult
from app.services.evaluator import coerce_date, coerce_decimal, is_null
from app.services.llm_clients import (
    ExtractionOutcome,
    LLMConfigurationError,
    build_client,
)
from app.utils.constants import BillStatus, ModelProvider
from app.utils.image_proc import ImageValidationError, validate_image_bytes

logger = logging.getLogger(__name__)

# Column length ceilings, mirrored from models.py so a chatty model cannot
# blow up the INSERT with a 40 kB "vendor name".
_MAX_VENDOR = 512
_MAX_BILL_NUMBER = 128
_MAX_CURRENCY = 8


class ExtractionError(RuntimeError):
    """Extraction could not be started (bad bill, missing file, bad provider)."""


class BillExtractorService:
    """Runs one or more vision models over a bill and stores the results."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    # ------------------------------------------------------------------ API
    async def process_bill(
        self,
        bill_id: uuid.UUID,
        image_path: str,
        models: Sequence[ModelProvider | str],
    ) -> list[ExtractionResult]:
        """Extract ``bill_id`` with every requested model.

        Models run concurrently: three sequential vision calls take 15-20
        seconds, three concurrent ones take as long as the slowest. Because
        every client swallows its own exceptions, a dead provider degrades that
        one row rather than the whole comparison.

        Re-running always INSERTs new rows -- results are an append-only audit
        log (see the idempotency note in ``models.ExtractionResult``).

        Raises:
            ExtractionError: Unknown bill, missing/invalid image, or a
                requested provider that has no API key.
        """
        bill = await self._session.get(Bill, bill_id)
        if bill is None:
            raise ExtractionError(f"Bill {bill_id} does not exist.")

        self._validate_source_image(image_path)
        clients = self._build_clients(models)

        bill.status = BillStatus.PROCESSING
        bill.error_message = None
        await self._session.commit()

        try:
            outcomes: list[ExtractionOutcome] = list(
                await asyncio.gather(
                    *(client.extract_bill_data(image_path) for client in clients)
                )
            )
        except Exception as exc:  # noqa: BLE001 - keep the bill out of PROCESSING limbo
            bill.status = BillStatus.FAILED
            bill.error_message = f"Extraction aborted: {exc}"
            await self._session.commit()
            logger.exception("Extraction run aborted for bill %s.", bill_id)
            raise ExtractionError(str(exc)) from exc

        rows = [self._to_row(bill_id, outcome) for outcome in outcomes]
        self._session.add_all(rows)

        any_success = any(outcome.succeeded for outcome in outcomes)
        bill.status = BillStatus.COMPLETED if any_success else BillStatus.FAILED
        if not any_success:
            bill.error_message = "; ".join(
                f"{o.model_name}: {o.error_message}" for o in outcomes if o.error_message
            )[:2000]

        await self._session.commit()
        for row in rows:
            await self._session.refresh(row)

        logger.info(
            "Bill %s extracted by %d model(s); %d succeeded.",
            bill_id,
            len(rows),
            sum(1 for o in outcomes if o.succeeded),
        )
        return rows

    async def list_results(self, bill_id: uuid.UUID) -> list[ExtractionResult]:
        """Every extraction attempt for a bill, newest last."""
        stmt = (
            select(ExtractionResult)
            .where(ExtractionResult.bill_id == bill_id)
            .order_by(ExtractionResult.created_at)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    # ------------------------------------------------------------- internal
    def _validate_source_image(self, image_path: str) -> None:
        """Re-check the stored file before spending money on an API call."""
        path = Path(image_path)
        if not path.is_file():
            raise ExtractionError(
                f"Image file is missing from disk: {path}. It may have been "
                "deleted after upload."
            )
        try:
            validate_image_bytes(
                path.read_bytes(), max_bytes=self._settings.max_image_size_bytes
            )
        except ImageValidationError as exc:
            raise ExtractionError(str(exc)) from exc
        except OSError as exc:
            raise ExtractionError(f"Could not read image: {exc}") from exc

    def _build_clients(self, models: Sequence[ModelProvider | str]):
        """Resolve provider slugs to clients, failing fast with a clear message."""
        if not models:
            raise ExtractionError("No models requested.")
        try:
            return [build_client(model, self._settings) for model in models]
        except LLMConfigurationError as exc:
            raise ExtractionError(str(exc)) from exc

    def _to_row(self, bill_id: uuid.UUID, outcome: ExtractionOutcome) -> ExtractionResult:
        """Convert a provider outcome into a database row.

        Type coercion happens here, once, so that every downstream consumer
        (evaluator, Zoho push, frontend) sees a real ``date`` and a real
        ``Decimal`` rather than whatever string the model felt like emitting.
        """
        fields = outcome.fields if outcome.succeeded else {}
        return ExtractionResult(
            bill_id=bill_id,
            model_name=outcome.model_name,
            provider=outcome.provider,
            vendor_name=_clean_str(fields.get("vendor_name"), _MAX_VENDOR),
            bill_number=_clean_str(fields.get("bill_number"), _MAX_BILL_NUMBER),
            date=_clean_date(fields.get("date")),
            amount=_clean_amount(fields.get("amount")),
            currency=_clean_currency(fields.get("currency")),
            tax_gst_details=_clean_str(fields.get("tax_gst_details"), None),
            raw_response=outcome.raw_response[:100_000],
            latency_ms=outcome.latency_ms,
            cost_usd=outcome.cost_usd,
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
            token_source=outcome.token_source,
            succeeded=outcome.succeeded,
            error_message=(outcome.error_message or None) and outcome.error_message[:2000],
        )


# --------------------------------------------------------------------------
# Coercion helpers
# --------------------------------------------------------------------------


def _clean_str(value: Any, max_length: int | None) -> str | None:
    """Trim, truncate, and map null-ish placeholders to ``None``."""
    if is_null(value):
        return None
    text = str(value).strip()
    return text[:max_length] if max_length else text


def _clean_currency(value: Any) -> str:
    """Uppercase ISO code, defaulting to INR for Indian bills."""
    if is_null(value):
        return "INR"
    return str(value).strip().upper()[:_MAX_CURRENCY] or "INR"


def _clean_date(value: Any) -> date_type | None:
    """Parse whatever the model emitted into a real date, or ``None``."""
    return coerce_date(value)


def _clean_amount(value: Any) -> Decimal | None:
    """Parse a monetary value, rejecting negatives and absurd magnitudes.

    A negative total on a kirana bill is always a parse artefact, and anything
    above 10 crore is a hallucinated digit run -- both are more honestly
    recorded as ``None`` than as a number that would poison the average.
    """
    amount = coerce_decimal(value)
    if amount is None:
        return None
    if amount < 0 or amount > Decimal("100000000"):
        logger.warning("Discarding implausible extracted amount: %s", amount)
        return None
    return amount.quantize(Decimal("0.01"))
