"""Field-level evaluation framework.

This is the heart of the exercise. A single "did the model get it right?"
boolean is useless for handwritten input: a model that reads
``Sharma Genral Store`` for ``Sharma General Store`` has done almost all of the
work, while a model that reads ``Verma Medicals`` has done none of it. Both
would score 0 under exact matching, so we score every field independently and
award partial credit on a documented scale.

Scoring scale
-------------
======================  =====  ==============================================
Outcome                 Score  ``match_type``
======================  =====  ==============================================
Identical (normalised)  1.0    ``exact``
``fuzz.ratio`` >= 90    0.9    ``fuzzy``
``fuzz.ratio`` >= 70    0.7    ``partial``
``fuzz.ratio`` <  70    0.0    ``missing``
Both null               1.0    ``not_applicable``
Expected set, got null  0.0    ``missing``
Expected null, got a
value (hallucination)   0.0    ``missing``
======================  =====  ==============================================

Dates and amounts bypass string matching entirely -- see
:meth:`EvaluatorService.score_date` and :meth:`EvaluatorService.score_amount`.

The module deliberately imports nothing from ``app.config`` or
``app.database``: the scoring logic is pure, which is what makes
``tests/test_evaluator.py`` runnable without a database or an API key.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from dateutil import parser as dateutil_parser
from thefuzz import fuzz

from app.utils.constants import (
    AMOUNT_NEAR_SCORE,
    AMOUNT_TOLERANCE_PCT,
    EXACT_SCORE,
    EXTRACTION_FIELDS,
    FUZZY_SCORE,
    FUZZY_THRESHOLD,
    MISS_SCORE,
    PARTIAL_SCORE,
    PARTIAL_THRESHOLD,
    MatchType,
)

logger = logging.getLogger(__name__)

# Indian bills are day-first. Explicit formats are tried before dateutil so that
# 03/04/2024 is unambiguously 3 April, never 4 March.
_DATE_FORMATS: Final[tuple[str, ...]] = (
    "%Y-%m-%d",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%d.%m.%Y",
    "%d-%m-%y",
    "%d/%m/%y",
    "%d.%m.%y",
    "%Y/%m/%d",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d, %Y",
    "%d-%b-%Y",
    "%d-%b-%y",
)

_WHITESPACE = re.compile(r"\s+")
_EDGE_PUNCT = re.compile(r"^[\s\.,;:\-_/\\|*#]+|[\s\.,;:\-_/\\|*#]+$")
# Pull the first number-like token out of "Rs. 1,245.50/-" style strings.
_AMOUNT_TOKEN = re.compile(r"-?\d+(?:\.\d+)?")
_GSTIN_BODY = r"\d{2}[A-Z]{5}\d{4}[A-Z][A-Z\d]Z[A-Z\d]"
_GSTIN = re.compile(rf"(?<![A-Z\d]){_GSTIN_BODY}(?![A-Z\d])")
_GSTIN_LOOSE = re.compile(_GSTIN_BODY)

# Values a model emits when it means "nothing here".
_NULL_TOKENS: Final[frozenset[str]] = frozenset(
    {"", "none", "null", "n/a", "na", "nil", "-", "--", "not available", "not found", "unknown"}
)


@dataclass(frozen=True, slots=True)
class ScoredField:
    """The evaluation of one field of one extraction."""

    field_name: str
    score: float
    match_type: MatchType
    predicted_value: str | None = None
    expected_value: str | None = None
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the API layer."""
        return {
            "field_name": self.field_name,
            "score": round(self.score, 4),
            "match_type": self.match_type.value,
            "predicted_value": self.predicted_value,
            "expected_value": self.expected_value,
            "notes": self.notes,
        }


# --------------------------------------------------------------------------
# Coercion helpers -- module level so the tests can exercise them directly
# --------------------------------------------------------------------------


def is_null(value: Any) -> bool:
    """True when a value means "the model found nothing".

    Treats ``None`` and the family of placeholder strings models like to emit
    (``"N/A"``, ``"null"``, ``"-"``) as equivalent, so a model is not punished
    for its choice of null sentinel.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in _NULL_TOKENS
    return False


def normalize_text(value: Any) -> str:
    """Case-fold, de-accent and collapse whitespace for comparison.

    Normalisation is deliberately conservative. Stripping punctuation entirely
    would let ``S.G.Store`` match ``SG Store`` at 100%, inflating every score;
    we only strip punctuation at the edges.
    """
    if value is None:
        return ""
    text = str(value)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = _WHITESPACE.sub(" ", text).strip()
    text = _EDGE_PUNCT.sub("", text)
    return text.casefold()


def coerce_date(value: Any) -> date_type | None:
    """Parse anything date-shaped into a :class:`datetime.date`.

    Explicit day-first formats are tried first, then ``dateutil`` with
    ``dayfirst=True``. Returns ``None`` rather than raising, because an
    unparseable date is a scoring outcome, not an error.
    """
    if is_null(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date_type):
        return value

    text = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        return dateutil_parser.parse(text, dayfirst=True, fuzzy=True).date()
    except (ValueError, OverflowError, TypeError):
        logger.debug("Unparseable date value: %r", value)
        return None


def coerce_decimal(value: Any) -> Decimal | None:
    """Parse a monetary value into :class:`~decimal.Decimal`.

    Handles ``"Rs. 1,245.50/-"``, ``"₹245"``, ``"245.50 INR"`` and bare
    numbers. Floats are routed through ``str()`` so 0.1 + 0.2 style binary
    artefacts never reach the comparison.
    """
    if is_null(value):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):  # bool is an int subclass; reject explicitly
        return None
    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None

    match = _AMOUNT_TOKEN.search(str(value).replace(",", ""))
    if match is None:
        logger.debug("No numeric token found in amount value: %r", value)
        return None
    try:
        return Decimal(match.group(0))
    except InvalidOperation:  # pragma: no cover - regex guarantees a valid literal
        logger.debug("Unparseable amount value: %r", value)
        return None


def extract_gstin(value: Any) -> str | None:
    """Pull a 15-character GSTIN out of a free-text tax string, if present."""
    if is_null(value):
        return None
    text = str(value).upper()
    if (match := _GSTIN.search(text)) is not None:
        return match.group(0)
    # Some bills write the GSTIN in spaced groups ("07 AABCU9603R 1ZX"), which
    # defeats the word-boundary anchors, so retry on a whitespace-free copy.
    if (match := _GSTIN_LOOSE.search(_WHITESPACE.sub("", text))) is not None:
        return match.group(0)
    return None


def _display(value: Any) -> str | None:
    """Render a value for storage in the score row."""
    return None if value is None else str(value)


# --------------------------------------------------------------------------
# Evaluator
# --------------------------------------------------------------------------


class EvaluatorService:
    """Scores extractions against ground truth, field by field."""

    def __init__(
        self,
        *,
        fuzzy_threshold: int = FUZZY_THRESHOLD,
        partial_threshold: int = PARTIAL_THRESHOLD,
        amount_tolerance_pct: float = AMOUNT_TOLERANCE_PCT,
    ) -> None:
        """Thresholds are injectable so a reviewer can re-run the whole
        benchmark under a stricter rubric without editing source."""
        if not 0 <= partial_threshold <= fuzzy_threshold <= 100:
            raise ValueError("Require 0 <= partial_threshold <= fuzzy_threshold <= 100.")
        self.fuzzy_threshold = fuzzy_threshold
        self.partial_threshold = partial_threshold
        self.amount_tolerance_pct = amount_tolerance_pct

    # ------------------------------------------------------------ null cases
    def _score_nulls(
        self, field_name: str, predicted: Any, expected: Any
    ) -> ScoredField | None:
        """Resolve the three null permutations, or return ``None`` to continue.

        The asymmetry is intentional. A model that says "I could not read this"
        when there was nothing to read is *correct* and scores 1.0. A model that
        invents a bill number where the bill had none is hallucinating, which is
        the single most damaging failure mode for an accounting pipeline, so it
        scores 0.0 -- never partial credit.
        """
        pred_null, exp_null = is_null(predicted), is_null(expected)

        if pred_null and exp_null:
            return ScoredField(
                field_name=field_name,
                score=EXACT_SCORE,
                match_type=MatchType.NOT_APPLICABLE,
                predicted_value=None,
                expected_value=None,
                notes="Field absent from the bill and correctly reported as null.",
            )
        if pred_null and not exp_null:
            return ScoredField(
                field_name=field_name,
                score=MISS_SCORE,
                match_type=MatchType.MISSING,
                predicted_value=None,
                expected_value=_display(expected),
                notes="Model returned null but a value was present on the bill.",
            )
        if exp_null and not pred_null:
            return ScoredField(
                field_name=field_name,
                score=MISS_SCORE,
                match_type=MatchType.MISSING,
                predicted_value=_display(predicted),
                expected_value=None,
                notes="Hallucination: model invented a value the bill does not contain.",
            )
        return None

    # ------------------------------------------------------------------ text
    def score_text(self, field_name: str, predicted: Any, expected: Any) -> ScoredField:
        """Normalised exact match, falling back to ``thefuzz.ratio``."""
        if (null_result := self._score_nulls(field_name, predicted, expected)) is not None:
            return null_result

        pred_norm = normalize_text(predicted)
        exp_norm = normalize_text(expected)

        if pred_norm == exp_norm:
            return ScoredField(
                field_name=field_name,
                score=EXACT_SCORE,
                match_type=MatchType.EXACT,
                predicted_value=_display(predicted),
                expected_value=_display(expected),
                notes="Identical after normalisation.",
            )

        ratio = fuzz.ratio(pred_norm, exp_norm)
        if ratio >= self.fuzzy_threshold:
            score, match_type = FUZZY_SCORE, MatchType.FUZZY
        elif ratio >= self.partial_threshold:
            score, match_type = PARTIAL_SCORE, MatchType.PARTIAL
        else:
            score, match_type = MISS_SCORE, MatchType.MISSING

        return ScoredField(
            field_name=field_name,
            score=score,
            match_type=match_type,
            predicted_value=_display(predicted),
            expected_value=_display(expected),
            notes=f"thefuzz.ratio = {ratio}.",
        )

    # ------------------------------------------------------------------ date
    def score_date(self, predicted: Any, expected: Any) -> ScoredField:
        """Compare dates semantically, never as strings.

        ``15/03/2024``, ``2024-03-15`` and ``15 Mar 2024`` are the same day and
        all score 1.0 -- we are grading reading comprehension, not output
        formatting. A value the parser cannot understand at all scores 0.0.
        """
        if (null_result := self._score_nulls("date", predicted, expected)) is not None:
            return null_result

        pred_date = coerce_date(predicted)
        exp_date = coerce_date(expected)

        if exp_date is None:
            return ScoredField(
                field_name="date",
                score=MISS_SCORE,
                match_type=MatchType.MISSING,
                predicted_value=_display(predicted),
                expected_value=_display(expected),
                notes="Ground-truth date is unparseable; fix the answer key.",
            )
        if pred_date is None:
            return ScoredField(
                field_name="date",
                score=MISS_SCORE,
                match_type=MatchType.MISSING,
                predicted_value=_display(predicted),
                expected_value=exp_date.isoformat(),
                notes="Predicted date could not be parsed into a calendar date.",
            )

        if pred_date == exp_date:
            same_string = str(predicted).strip() == str(expected).strip()
            return ScoredField(
                field_name="date",
                score=EXACT_SCORE,
                match_type=MatchType.EXACT,
                predicted_value=pred_date.isoformat(),
                expected_value=exp_date.isoformat(),
                notes=None if same_string else "Same calendar date, different input format.",
            )

        delta_days = abs((pred_date - exp_date).days)
        transposed = (
            pred_date.day == exp_date.month
            and pred_date.month == exp_date.day
            and pred_date.year == exp_date.year
        )
        note = f"Off by {delta_days} day(s)."
        if transposed:
            note += " Day/month appear transposed (US-style date reading)."

        return ScoredField(
            field_name="date",
            score=MISS_SCORE,
            match_type=MatchType.MISSING,
            predicted_value=pred_date.isoformat(),
            expected_value=exp_date.isoformat(),
            notes=note,
        )

    # ---------------------------------------------------------------- amount
    def score_amount(self, predicted: Any, expected: Any) -> ScoredField:
        """Compare amounts numerically with a 1% tolerance band.

        Exact to the paisa scores 1.0. Within 1% scores 0.9 -- that band covers
        a misread final digit or a dropped paisa, which a human reviewer fixes
        in two seconds, and is meaningfully different from reading the subtotal
        instead of the total.
        """
        if (null_result := self._score_nulls("amount", predicted, expected)) is not None:
            return null_result

        pred_amount = coerce_decimal(predicted)
        exp_amount = coerce_decimal(expected)

        if exp_amount is None:
            return ScoredField(
                field_name="amount",
                score=MISS_SCORE,
                match_type=MatchType.MISSING,
                predicted_value=_display(predicted),
                expected_value=_display(expected),
                notes="Ground-truth amount is unparseable; fix the answer key.",
            )
        if pred_amount is None:
            return ScoredField(
                field_name="amount",
                score=MISS_SCORE,
                match_type=MatchType.MISSING,
                predicted_value=_display(predicted),
                expected_value=str(exp_amount),
                notes="Predicted amount could not be parsed as a number.",
            )

        if pred_amount == exp_amount:
            return ScoredField(
                field_name="amount",
                score=EXACT_SCORE,
                match_type=MatchType.EXACT,
                predicted_value=str(pred_amount),
                expected_value=str(exp_amount),
                notes=None,
            )

        difference = abs(pred_amount - exp_amount)
        if exp_amount == 0:
            relative = Decimal("1")
        else:
            relative = difference / abs(exp_amount)

        if relative <= Decimal(str(self.amount_tolerance_pct)):
            return ScoredField(
                field_name="amount",
                score=AMOUNT_NEAR_SCORE,
                match_type=MatchType.FUZZY,
                predicted_value=str(pred_amount),
                expected_value=str(exp_amount),
                notes=f"Within tolerance: off by {relative * 100:.2f}%.",
            )

        return ScoredField(
            field_name="amount",
            score=MISS_SCORE,
            match_type=MatchType.MISSING,
            predicted_value=str(pred_amount),
            expected_value=str(exp_amount),
            notes=f"Off by {relative * 100:.2f}% ({difference}).",
        )

    # ------------------------------------------------------------------- tax
    def score_tax(self, predicted: Any, expected: Any) -> ScoredField:
        """GSTIN-aware comparison of the tax field.

        When both sides contain a well-formed 15-character GSTIN we compare
        those directly: a GSTIN is a checksum-bearing identifier where one wrong
        character makes it useless, so near-misses must not earn fuzzy credit.
        Otherwise the field is free text and falls back to fuzzy matching.
        """
        if (null_result := self._score_nulls("tax_gst_details", predicted, expected)) is not None:
            return null_result

        pred_gstin = extract_gstin(predicted)
        exp_gstin = extract_gstin(expected)

        if pred_gstin and exp_gstin:
            matched = pred_gstin == exp_gstin
            return ScoredField(
                field_name="tax_gst_details",
                score=EXACT_SCORE if matched else MISS_SCORE,
                match_type=MatchType.EXACT if matched else MatchType.MISSING,
                predicted_value=_display(predicted),
                expected_value=_display(expected),
                notes=(
                    "GSTIN matched exactly."
                    if matched
                    else f"GSTIN mismatch: read {pred_gstin}, expected {exp_gstin}."
                ),
            )

        return self.score_text("tax_gst_details", predicted, expected)

    # -------------------------------------------------------------- dispatch
    def score_field(self, field_name: str, predicted: Any, expected: Any) -> ScoredField:
        """Route one field to its type-appropriate scorer."""
        if field_name == "date":
            return self.score_date(predicted, expected)
        if field_name == "amount":
            return self.score_amount(predicted, expected)
        if field_name == "tax_gst_details":
            return self.score_tax(predicted, expected)
        return self.score_text(field_name, predicted, expected)

    def evaluate(
        self, predicted: Mapping[str, Any], expected: Mapping[str, Any]
    ) -> dict[str, ScoredField]:
        """Score all six fields of one extraction."""
        return {
            field: self.score_field(field, predicted.get(field), expected.get(field))
            for field in EXTRACTION_FIELDS
        }

    @staticmethod
    def overall_accuracy(scores: Mapping[str, ScoredField]) -> float:
        """Unweighted mean of the field scores.

        Unweighted on purpose: weighting ``amount`` more heavily would produce a
        prettier headline number but would hide the fact that a model is losing
        the vendor name, which is exactly what a bookkeeping pipeline needs to
        know. Downstream consumers can re-weight from the per-field breakdown.
        """
        if not scores:
            return 0.0
        return round(sum(s.score for s in scores.values()) / len(scores), 4)

    @staticmethod
    def dominant_match_type(match_types: list[MatchType]) -> MatchType:
        """Most frequent match type, ties broken toward the stricter label."""
        if not match_types:
            return MatchType.MISSING
        order = [
            MatchType.EXACT,
            MatchType.NOT_APPLICABLE,
            MatchType.FUZZY,
            MatchType.PARTIAL,
            MatchType.MISSING,
        ]
        counts = {mt: match_types.count(mt) for mt in order}
        best = max(counts.values())
        for mt in reversed(order):
            if counts[mt] == best:
                return mt
        return MatchType.MISSING  # pragma: no cover - unreachable
