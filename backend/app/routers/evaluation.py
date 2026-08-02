"""Ground-truth management and scoring endpoints."""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.dependencies import DBSession
from app.models import Bill, EvaluationScore, ExtractionResult, GroundTruth
from app.schemas import (
    AggregateReportResponse,
    BillEvaluationResponse,
    EvaluationReport,
    ExtractionEvaluation,
    FieldAccuracy,
    FieldScore,
    GroundTruthCreate,
    GroundTruthResponse,
)
from app.services.evaluator import EvaluatorService, ScoredField
from app.utils.constants import EXTRACTION_FIELDS, EXTRAPOLATION_BILL_COUNT, MatchType

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["evaluation"])
evaluator = EvaluatorService()


# --------------------------------------------------------------------------
# Ground truth
# --------------------------------------------------------------------------


@router.post(
    "/ground-truth/{bill_id}",
    response_model=GroundTruthResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit or update the answer key for a bill",
)
async def submit_ground_truth(
    bill_id: uuid.UUID, payload: GroundTruthCreate, session: DBSession
) -> GroundTruthResponse:
    """Create or replace the human-verified answer key.

    Upsert rather than insert-only: reading handwriting is genuinely hard and
    the first pass at an answer key is often wrong. Correcting it must not
    require deleting the bill and re-uploading it.

    Any previously computed scores for this bill are discarded, because they
    were measured against the old key and would otherwise silently misreport.
    """
    if await session.get(Bill, bill_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Bill {bill_id} not found.")

    existing = (
        await session.execute(select(GroundTruth).where(GroundTruth.bill_id == bill_id))
    ).scalar_one_or_none()

    if existing is None:
        record = GroundTruth(bill_id=bill_id, **payload.model_dump())
        session.add(record)
    else:
        for key, value in payload.model_dump().items():
            setattr(existing, key, value)
        record = existing
        await _clear_scores_for_bill(session, bill_id)

    await session.commit()
    await session.refresh(record)
    return GroundTruthResponse.model_validate(record)


@router.get(
    "/ground-truth/{bill_id}",
    response_model=GroundTruthResponse,
    summary="Read the answer key for a bill",
)
async def get_ground_truth(bill_id: uuid.UUID, session: DBSession) -> GroundTruthResponse:
    """Fetch the stored answer key, or 404 if none has been submitted."""
    record = (
        await session.execute(select(GroundTruth).where(GroundTruth.bill_id == bill_id))
    ).scalar_one_or_none()
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No ground truth submitted for bill {bill_id} yet.",
        )
    return GroundTruthResponse.model_validate(record)


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


@router.post(
    "/evaluate/{bill_id}",
    response_model=BillEvaluationResponse,
    summary="Score every extraction for a bill against its ground truth",
)
async def evaluate_bill(bill_id: uuid.UUID, session: DBSession) -> BillEvaluationResponse:
    """Run the scoring rubric over each extraction attached to this bill.

    Idempotent: existing scores for the bill are deleted and recomputed, so
    calling this twice gives the same answer rather than accumulating duplicate
    rows.
    """
    truth = (
        await session.execute(select(GroundTruth).where(GroundTruth.bill_id == bill_id))
    ).scalar_one_or_none()
    if truth is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Bill {bill_id} has no ground truth. Submit it via "
                f"POST /api/ground-truth/{bill_id} before evaluating."
            ),
        )

    all_attempts = list(
        (
            await session.execute(
                select(ExtractionResult)
                .where(ExtractionResult.bill_id == bill_id, ExtractionResult.succeeded.is_(True))
                .order_by(ExtractionResult.created_at)
            )
        )
        .scalars()
        .all()
    )
    extractions = latest_per_model(all_attempts)
    if not extractions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Bill {bill_id} has no successful extractions to score. "
                f"Run POST /api/extract/{bill_id} first."
            ),
        )

    await _clear_scores_for_bill(session, bill_id)
    expected = _as_mapping(truth)
    evaluations: list[ExtractionEvaluation] = []

    for extraction in extractions:
        scored = evaluator.evaluate(_as_mapping(extraction), expected)
        session.add_all(
            EvaluationScore(
                extraction_result_id=extraction.id,
                field_name=field.field_name,
                score=field.score,
                match_type=field.match_type,
                predicted_value=field.predicted_value,
                expected_value=field.expected_value,
                notes=field.notes,
            )
            for field in scored.values()
        )
        evaluations.append(
            ExtractionEvaluation(
                extraction_result_id=extraction.id,
                model_name=extraction.model_name,
                overall_accuracy=evaluator.overall_accuracy(scored),
                fields={name: _to_field_score(sf) for name, sf in scored.items()},
                cost_usd=extraction.cost_usd,
                latency_ms=extraction.latency_ms,
            )
        )

    await session.commit()
    logger.info("Scored %d extraction(s) for bill %s.", len(evaluations), bill_id)
    return BillEvaluationResponse(bill_id=bill_id, evaluations=evaluations)


@router.get(
    "/evaluation/report",
    response_model=AggregateReportResponse,
    summary="Leaderboard across every scored bill",
)
async def evaluation_report(session: DBSession) -> AggregateReportResponse:
    """Aggregate every stored score into one per-model report.

    Only the newest run per (bill, model) is counted. Re-running a bill would
    otherwise let that bill vote twice for that model.

    Costs are extrapolated to 100 bills because that is the unit a finance team
    actually reasons in. The extrapolation is deliberately naive -- mean cost
    times 100 -- and its limitations are documented in ``docs/METHODOLOGY.md``.
    """
    stmt = (
        select(ExtractionResult)
        .options(selectinload(ExtractionResult.scores))
        .where(ExtractionResult.succeeded.is_(True))
        .order_by(ExtractionResult.created_at)
    )
    extractions = list((await session.execute(stmt)).scalars().all())
    scored = [e for e in extractions if e.scores]

    # One measurement per (bill, model), newest wins.
    by_bill: dict[Any, list[ExtractionResult]] = defaultdict(list)
    for extraction in scored:
        by_bill[extraction.bill_id].append(extraction)
    deduped = [row for rows in by_bill.values() for row in latest_per_model(rows)]

    by_model: dict[str, list[ExtractionResult]] = defaultdict(list)
    for extraction in deduped:
        by_model[extraction.model_name].append(extraction)

    attempts_by_model: dict[str, list[ExtractionResult]] = defaultdict(list)
    for extraction in extractions:
        attempts_by_model[extraction.model_name].append(extraction)

    reports = [
        _build_report(model_name, rows, attempts_by_model.get(model_name, rows))
        for model_name, rows in by_model.items()
    ]
    reports.sort(key=lambda r: (-r.overall_accuracy, r.cost_per_100_bills_usd))

    bills_with_truth = len({e.bill_id for e in deduped})
    return AggregateReportResponse(
        generated_at=datetime.now(timezone.utc),
        bills_with_ground_truth=bills_with_truth,
        reports=reports,
        recommendation=_recommendation(reports, bills_with_truth),
    )


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


async def _clear_scores_for_bill(session: AsyncSession, bill_id: uuid.UUID) -> None:
    """Delete every score belonging to a bill's extractions."""
    subquery = select(ExtractionResult.id).where(ExtractionResult.bill_id == bill_id)
    await session.execute(
        delete(EvaluationScore).where(EvaluationScore.extraction_result_id.in_(subquery))
    )


def latest_per_model(rows: list[ExtractionResult]) -> list[ExtractionResult]:
    """Collapse repeated runs to the newest attempt per model.

    Extraction results are an append-only audit log, so running a bill twice
    leaves two rows for the same model. That is correct for the log but wrong
    everywhere a *comparison* is drawn: the grid grows a duplicate column, and
    the leaderboard counts that bill twice for that model, quietly weighting the
    average toward whichever bill happened to be re-run.

    Ordering is by ``created_at`` with the database id as a tiebreaker, since
    two runs seconds apart can share a timestamp at second resolution.
    """
    newest: dict[str, ExtractionResult] = {}
    for row in sorted(rows, key=lambda r: (r.created_at, str(r.id))):
        newest[row.model_name] = row
    return sorted(newest.values(), key=lambda r: r.created_at)


def _as_mapping(record: ExtractionResult | GroundTruth) -> dict[str, Any]:
    """Project an ORM row onto the six evaluated fields."""
    return {field: getattr(record, field, None) for field in EXTRACTION_FIELDS}


def _to_field_score(scored: ScoredField) -> FieldScore:
    return FieldScore(
        field_name=scored.field_name,
        score=round(scored.score, 4),
        match_type=scored.match_type,
        predicted_value=scored.predicted_value,
        expected_value=scored.expected_value,
        notes=scored.notes,
    )


def _build_report(
    model_name: str, scored_rows: list[ExtractionResult], all_attempts: list[ExtractionResult]
) -> EvaluationReport:
    """Aggregate one model's scored extractions into an :class:`EvaluationReport`."""
    per_field_scores: dict[str, list[float]] = defaultdict(list)
    per_field_types: dict[str, list[MatchType]] = defaultdict(list)

    for row in scored_rows:
        for score in row.scores:
            per_field_scores[score.field_name].append(score.score)
            per_field_types[score.field_name].append(MatchType(score.match_type))

    fields: dict[str, FieldAccuracy] = {}
    for field_name in EXTRACTION_FIELDS:
        values = per_field_scores.get(field_name, [])
        types = per_field_types.get(field_name, [])
        counts: dict[str, int] = defaultdict(int)
        for match_type in types:
            counts[match_type.value] += 1
        fields[field_name] = FieldAccuracy(
            accuracy=round(sum(values) / len(values), 4) if values else 0.0,
            match_type=EvaluatorService.dominant_match_type(types),
            sample_size=len(values),
            match_type_counts=dict(counts),
        )

    all_scores = [s for values in per_field_scores.values() for s in values]
    overall = round(sum(all_scores) / len(all_scores), 4) if all_scores else 0.0

    total_cost = sum(row.cost_usd for row in scored_rows)
    bill_count = len(scored_rows) or 1
    avg_cost = total_cost / bill_count
    avg_latency = sum(row.latency_ms for row in scored_rows) / bill_count

    attempts = len(all_attempts) or 1
    success_rate = sum(1 for row in all_attempts if row.succeeded) / attempts

    return EvaluationReport(
        model_name=model_name,
        provider=scored_rows[0].provider if scored_rows else "unknown",
        bills_evaluated=len(scored_rows),
        overall_accuracy=overall,
        fields=fields,
        total_cost_usd=round(total_cost, 6),
        avg_cost_per_bill_usd=round(avg_cost, 6),
        cost_per_100_bills_usd=round(avg_cost * EXTRAPOLATION_BILL_COUNT, 4),
        avg_latency_ms=round(avg_latency, 1),
        success_rate=round(success_rate, 4),
    )


def _recommendation(reports: list[EvaluationReport], bill_count: int) -> str | None:
    """Turn the leaderboard into one honest sentence.

    Explicitly refuses to declare a winner on a sample too small to support one:
    on 10-15 bills a 3-point accuracy gap is noise, and saying so is more useful
    than a confident ranking that will not replicate.
    """
    if not reports:
        return None

    best = reports[0]
    cheapest = min(reports, key=lambda r: r.cost_per_100_bills_usd)
    fastest = min(reports, key=lambda r: r.avg_latency_ms)

    lines = [
        f"Most accurate: {best.model_name} at {best.overall_accuracy:.1%} "
        f"over {best.bills_evaluated} bill(s), ${best.cost_per_100_bills_usd:.2f} per 100 bills.",
        f"Cheapest: {cheapest.model_name} at ${cheapest.cost_per_100_bills_usd:.2f} per 100 bills "
        f"({cheapest.overall_accuracy:.1%} accuracy).",
        f"Fastest: {fastest.model_name} at {fastest.avg_latency_ms:.0f} ms average.",
    ]

    if bill_count < 30:
        lines.append(
            f"Caveat: only {bill_count} bill(s) carry ground truth. At this sample size the "
            "95% confidence interval on any accuracy figure is roughly +/- 15 points, so "
            "treat differences under ~10 points as noise rather than a ranking."
        )

    if len(reports) > 1:
        runner_up = reports[1]
        gap = best.overall_accuracy - runner_up.overall_accuracy
        savings = runner_up.cost_per_100_bills_usd - best.cost_per_100_bills_usd
        if gap < 0.05 and savings > 0:
            lines.append(
                f"{runner_up.model_name} is within {gap:.1%} of the leader and cheaper by "
                f"${savings:.2f} per 100 bills -- the better default unless every "
                "percentage point of accuracy is worth the spend."
            )

    return " ".join(lines)
