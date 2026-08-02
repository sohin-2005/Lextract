"""Bill upload and retrieval endpoints."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.dependencies import AppSettings, DBSession
from app.models import Bill, ExtractionResult, GroundTruth
from app.schemas import BillDetail, BillSummary, BillUploadResponse
from app.utils.constants import BillStatus
from app.utils.image_proc import ImageValidationError, safe_filename, validate_image_bytes

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bills", tags=["bills"])


@router.post(
    "/upload",
    response_model=BillUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a bill image",
)
async def upload_bill(
    session: DBSession,
    settings: AppSettings,
    file: UploadFile = File(..., description="JPEG, PNG or WebP photo of a handwritten bill."),
) -> BillUploadResponse:
    """Validate and store one bill image.

    The whole file is read into memory before validation. That is safe because
    ``MAX_IMAGE_SIZE_MB`` caps it at 10 MB, and it means a malicious file is
    rejected on its magic bytes before anything is ever written to disk.
    """
    try:
        payload = await file.read()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Could not read upload: {exc}"
        ) from exc
    finally:
        await file.close()

    try:
        info = validate_image_bytes(payload, max_bytes=settings.max_image_size_bytes)
    except ImageValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    stored_name = safe_filename(file.filename or "bill", extension=info.extension)
    destination = settings.upload_dir / stored_name
    try:
        destination.write_bytes(payload)
    except OSError as exc:
        logger.exception("Failed to persist upload to %s", destination)
        raise HTTPException(
            status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
            detail=f"Could not write image to disk: {exc}",
        ) from exc

    bill = Bill(
        filename=file.filename or stored_name,
        original_path=str(destination),
        content_type=info.mime_type,
        size_bytes=info.size_bytes,
        status=BillStatus.UPLOADED,
    )
    session.add(bill)
    await session.commit()
    await session.refresh(bill)

    logger.info("Stored bill %s (%s, %d bytes).", bill.id, info.mime_type, info.size_bytes)
    return BillUploadResponse.model_validate(bill)


@router.get("", response_model=list[BillSummary], summary="List all bills")
async def list_bills(session: DBSession) -> list[BillSummary]:
    """Dashboard listing, newest first, with per-bill counts.

    Counts are computed in SQL rather than by loading every extraction row, so
    the dashboard stays a single fast query as the dataset grows.
    """
    extraction_counts = (
        select(ExtractionResult.bill_id, func.count().label("n"))
        .group_by(ExtractionResult.bill_id)
        .subquery()
    )
    stmt = (
        select(
            Bill,
            func.coalesce(extraction_counts.c.n, 0),
            GroundTruth.id.isnot(None),
        )
        .outerjoin(extraction_counts, extraction_counts.c.bill_id == Bill.id)
        .outerjoin(GroundTruth, GroundTruth.bill_id == Bill.id)
        .order_by(Bill.uploaded_at.desc())
    )

    rows = (await session.execute(stmt)).all()
    return [
        BillSummary(
            id=bill.id,
            filename=bill.filename,
            status=bill.status,
            uploaded_at=bill.uploaded_at,
            extraction_count=int(count or 0),
            has_ground_truth=bool(has_gt),
        )
        for bill, count, has_gt in rows
    ]


@router.get("/{bill_id}", response_model=BillDetail, summary="Get one bill with its extractions")
async def get_bill(bill_id: uuid.UUID, session: DBSession) -> BillDetail:
    """Full detail for the bill-detail page."""
    stmt = (
        select(Bill)
        .where(Bill.id == bill_id)
        .options(
            selectinload(Bill.extraction_results),
            selectinload(Bill.ground_truth),
        )
    )
    bill = (await session.execute(stmt)).scalar_one_or_none()
    if bill is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Bill {bill_id} not found.")

    return BillDetail(
        id=bill.id,
        filename=bill.filename,
        status=bill.status,
        uploaded_at=bill.uploaded_at,
        content_type=bill.content_type,
        size_bytes=bill.size_bytes,
        error_message=bill.error_message,
        image_url=f"/api/bills/{bill.id}/image",
        extraction_results=[r for r in bill.extraction_results],  # type: ignore[misc]
        ground_truth=bill.ground_truth,  # type: ignore[arg-type]
    )


@router.get("/{bill_id}/image", summary="Serve the stored bill image")
async def get_bill_image(bill_id: uuid.UUID, session: DBSession, settings: AppSettings) -> FileResponse:
    """Stream the image back to the frontend.

    The stored path is re-resolved and confirmed to sit inside ``UPLOAD_DIR``
    before anything is served. Paths in the database should always be safe --
    ``safe_filename`` sees to that -- but an endpoint that turns a database
    string into a filesystem read is exactly where directory traversal creeps
    in, so it is checked here too.
    """
    bill = await session.get(Bill, bill_id)
    if bill is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Bill {bill_id} not found.")

    path = Path(bill.original_path).resolve()
    upload_root = settings.upload_dir.resolve()
    if not path.is_relative_to(upload_root):
        logger.error("Refusing to serve %s: outside upload dir %s", path, upload_root)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Image path is outside the upload directory."
        )
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Image file is no longer on disk. Re-upload the bill.",
        )

    return FileResponse(path, media_type=bill.content_type, filename=bill.filename)


@router.delete(
    "/{bill_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a bill and its results"
)
async def delete_bill(bill_id: uuid.UUID, session: DBSession, settings: AppSettings) -> None:
    """Remove a bill, its image and every cascading row.

    Useful during dataset curation when a bill turns out to contain PII that the
    redaction pass missed.
    """
    bill = await session.get(Bill, bill_id)
    if bill is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Bill {bill_id} not found.")

    path = Path(bill.original_path).resolve()
    await session.delete(bill)
    await session.commit()

    if path.is_relative_to(settings.upload_dir.resolve()) and path.is_file():
        try:
            path.unlink()
        except OSError as exc:  # pragma: no cover - best effort
            logger.warning("Deleted bill %s but could not remove %s: %s", bill_id, path, exc)
