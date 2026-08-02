"""Extraction endpoints: run models over a bill and read back the results."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, func, select

from app.dependencies import AppSettings, DBSession
from app.models import Bill, ExtractionResult
from app.schemas import ExtractionRequest, ExtractionResponse, ExtractionRunResponse
from app.services.extractors import BillExtractorService, ExtractionError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["extraction"])


@router.post(
    "/extract/{bill_id}",
    response_model=ExtractionRunResponse,
    summary="Run one or more models over a bill",
)
async def extract_bill(
    bill_id: uuid.UUID,
    request: ExtractionRequest,
    session: DBSession,
    settings: AppSettings,
) -> ExtractionRunResponse:
    """Extract structured fields from a bill with every requested model.

    Synchronous by design. A background-job queue would be the right call in
    production, but for a 10-15 bill benchmark it would add Celery and Redis to
    the setup burden while making the comparison *harder* to observe. Models run
    concurrently, so a three-way run finishes in roughly the latency of the
    slowest single model.

    A provider that fails is recorded as a failed row with its error message
    rather than aborting the run -- you still get the comparison for the
    providers that worked.
    """
    bill = await session.get(Bill, bill_id)
    if bill is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Bill {bill_id} not found.")

    unconfigured = [m.value for m in request.models if m.value not in settings.configured_providers]
    if unconfigured:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"No API key configured for: {', '.join(unconfigured)}. "
                f"Currently available: {', '.join(settings.configured_providers) or 'none'}. "
                "Add the missing key to backend/.env and restart the server."
            ),
        )

    service = BillExtractorService(session, settings)
    try:
        results = await service.process_bill(bill_id, bill.original_path, request.models)
    except ExtractionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unhandled extraction failure for bill %s.", bill_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Extraction failed unexpectedly: {exc}",
        ) from exc

    await session.refresh(bill)
    return ExtractionRunResponse(
        bill_id=bill_id,
        status=bill.status,
        requested_models=[m.value for m in request.models],
        succeeded_models=[r.model_name for r in results if r.succeeded],
        failed_models=[r.model_name for r in results if not r.succeeded],
        results=[ExtractionResponse.model_validate(r) for r in results],
    )


@router.delete(
    "/extract/results/disabled",
    summary="Delete results from providers no longer in ENABLED_PROVIDERS",
)
async def prune_disabled_results(session: DBSession, settings: AppSettings) -> dict[str, object]:
    """Remove extraction rows produced by providers you have since disabled.

    Results are an append-only audit log, so narrowing ``ENABLED_PROVIDERS``
    hides a provider from the UI but leaves its old rows in the comparison grid
    and the leaderboard. Rather than silently filtering them out of every read
    -- which would mean the report quietly disagrees with the database -- this
    deletes them explicitly, once, when you ask.

    Cascades to the evaluation scores and any Zoho mapping attached to those
    rows. Returns the number deleted so a no-op is obvious.
    """
    allowed = settings.enabled_provider_list
    if not allowed:
        return {
            "deleted": 0,
            "message": "ENABLED_PROVIDERS is blank, so every provider is allowed.",
        }

    stale = select(ExtractionResult).where(ExtractionResult.provider.notin_(allowed))
    doomed = list((await session.execute(stale)).scalars().all())
    if not doomed:
        return {"deleted": 0, "message": "No results from disabled providers.", "providers": []}

    providers = sorted({row.provider for row in doomed})
    # ORM-level delete so cascades fire for scores and Zoho mappings.
    for row in doomed:
        await session.delete(row)
    await session.commit()

    logger.info("Pruned %d extraction result(s) from %s.", len(doomed), ", ".join(providers))
    return {
        "deleted": len(doomed),
        "providers": providers,
        "message": f"Deleted {len(doomed)} result(s) from: {', '.join(providers)}.",
    }


@router.get(
    "/extract/{bill_id}/results",
    response_model=list[ExtractionResponse],
    summary="All extraction results for a bill",
)
async def get_extraction_results(
    bill_id: uuid.UUID, session: DBSession, settings: AppSettings
) -> list[ExtractionResponse]:
    """Return every extraction attempt for a bill, oldest first."""
    if await session.get(Bill, bill_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Bill {bill_id} not found.")

    service = BillExtractorService(session, settings)
    results = await service.list_results(bill_id)
    return [ExtractionResponse.model_validate(r) for r in results]
