"""Unit tests for the scoring framework.

The evaluator is the component whose correctness the whole benchmark rests on,
so it is tested in isolation: no database, no network, no API keys. Anyone can
clone the repo and run ``pytest`` in under a second.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app.services.evaluator import (
    EvaluatorService,
    coerce_date,
    coerce_decimal,
    extract_gstin,
    is_null,
    normalize_text,
)
from app.utils.constants import EXTRACTION_FIELDS, MatchType


@pytest.fixture()
def evaluator() -> EvaluatorService:
    """Evaluator with the default production thresholds."""
    return EvaluatorService()


# ---------------------------------------------------------------------------
# Exact matching
# ---------------------------------------------------------------------------


class TestExactMatch:
    def test_identical_strings_score_one(self, evaluator: EvaluatorService) -> None:
        result = evaluator.score_field("vendor_name", "Sharma General Store", "Sharma General Store")
        assert result.score == 1.0
        assert result.match_type is MatchType.EXACT

    @pytest.mark.parametrize(
        "predicted",
        [
            "  Sharma General Store  ",   # leading / trailing whitespace
            "SHARMA GENERAL STORE",       # case
            "Sharma  General   Store",    # collapsed internal whitespace
            "Sharma General Store.",      # trailing punctuation
        ],
    )
    def test_normalisation_still_counts_as_exact(
        self, evaluator: EvaluatorService, predicted: str
    ) -> None:
        result = evaluator.score_field("vendor_name", predicted, "Sharma General Store")
        assert result.score == 1.0
        assert result.match_type is MatchType.EXACT

    def test_currency_case_insensitive(self, evaluator: EvaluatorService) -> None:
        assert evaluator.score_field("currency", "inr", "INR").score == 1.0


# ---------------------------------------------------------------------------
# Fuzzy matching (thefuzz integration)
# ---------------------------------------------------------------------------


class TestFuzzyMatch:
    def test_single_dropped_letter_is_fuzzy(self, evaluator: EvaluatorService) -> None:
        """A dropped 'e' -- the classic handwriting misread -- earns 0.9."""
        result = evaluator.score_field("vendor_name", "Sharma Genral Store", "Sharma General Store")
        assert result.match_type is MatchType.FUZZY
        assert result.score == 0.9

    def test_moderate_divergence_is_partial(self, evaluator: EvaluatorService) -> None:
        result = evaluator.score_field("vendor_name", "Sharma Store", "Sharma General Store")
        assert result.match_type is MatchType.PARTIAL
        assert result.score == 0.7

    def test_wrong_vendor_scores_zero(self, evaluator: EvaluatorService) -> None:
        result = evaluator.score_field("vendor_name", "Verma Medicals", "Sharma General Store")
        assert result.match_type is MatchType.MISSING
        assert result.score == 0.0

    def test_ratio_is_reported_in_notes(self, evaluator: EvaluatorService) -> None:
        result = evaluator.score_field("vendor_name", "Sharma Genral Store", "Sharma General Store")
        assert result.notes is not None
        assert "thefuzz.ratio" in result.notes

    def test_thresholds_are_injectable(self) -> None:
        """A stricter rubric can be applied without touching source."""
        strict = EvaluatorService(fuzzy_threshold=99, partial_threshold=95)
        result = strict.score_field("vendor_name", "Sharma Genral Store", "Sharma General Store")
        assert result.match_type is MatchType.PARTIAL

    def test_invalid_threshold_ordering_rejected(self) -> None:
        with pytest.raises(ValueError, match="partial_threshold"):
            EvaluatorService(fuzzy_threshold=50, partial_threshold=80)


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


class TestDateScoring:
    @pytest.mark.parametrize(
        "predicted",
        ["2024-03-15", "15/03/2024", "15-03-2024", "15.03.2024", "15 Mar 2024", "15-Mar-2024"],
    )
    def test_same_day_different_format_scores_one(
        self, evaluator: EvaluatorService, predicted: str
    ) -> None:
        """Formatting is not comprehension: every spelling of the same day is 1.0."""
        result = evaluator.score_field("date", predicted, "2024-03-15")
        assert result.score == 1.0
        assert result.match_type is MatchType.EXACT

    def test_date_object_and_string_compare_equal(self, evaluator: EvaluatorService) -> None:
        assert evaluator.score_field("date", date(2024, 3, 15), "2024-03-15").score == 1.0

    def test_two_digit_year_expands(self, evaluator: EvaluatorService) -> None:
        assert evaluator.score_field("date", "15/03/24", "2024-03-15").score == 1.0

    def test_day_first_not_month_first(self, evaluator: EvaluatorService) -> None:
        """04/03/2024 on an Indian bill is 4 March, not 3 April."""
        assert coerce_date("04/03/2024") == date(2024, 3, 4)

    def test_transposed_day_month_detected(self, evaluator: EvaluatorService) -> None:
        result = evaluator.score_field("date", "2024-04-03", "2024-03-04")
        assert result.score == 0.0
        assert result.notes is not None and "transposed" in result.notes

    def test_wrong_date_scores_zero(self, evaluator: EvaluatorService) -> None:
        result = evaluator.score_field("date", "2024-03-20", "2024-03-15")
        assert result.score == 0.0
        assert result.match_type is MatchType.MISSING

    def test_unparseable_prediction_scores_zero(self, evaluator: EvaluatorService) -> None:
        result = evaluator.score_field("date", "illegible smudge", "2024-03-15")
        assert result.score == 0.0

    def test_coerce_date_returns_none_on_garbage(self) -> None:
        assert coerce_date("!!!") is None
        assert coerce_date(None) is None


# ---------------------------------------------------------------------------
# Amounts
# ---------------------------------------------------------------------------


class TestAmountScoring:
    def test_exact_amount(self, evaluator: EvaluatorService) -> None:
        result = evaluator.score_field("amount", "245.50", Decimal("245.50"))
        assert result.score == 1.0
        assert result.match_type is MatchType.EXACT

    def test_numeric_equality_across_types(self, evaluator: EvaluatorService) -> None:
        assert evaluator.score_field("amount", 245.5, Decimal("245.50")).score == 1.0

    @pytest.mark.parametrize(
        "raw", ["Rs. 245.50", "₹245.50", "245.50 INR", "245.50/-", "  245.50  "]
    )
    def test_currency_decoration_is_stripped(
        self, evaluator: EvaluatorService, raw: str
    ) -> None:
        assert evaluator.score_field("amount", raw, Decimal("245.50")).score == 1.0

    def test_thousands_separator(self, evaluator: EvaluatorService) -> None:
        assert evaluator.score_field("amount", "1,245.50", Decimal("1245.50")).score == 1.0

    def test_within_one_percent_scores_point_nine(self, evaluator: EvaluatorService) -> None:
        """1000.00 read as 1005.00 is 0.5% off -- inside tolerance."""
        result = evaluator.score_field("amount", Decimal("1005.00"), Decimal("1000.00"))
        assert result.score == 0.9
        assert result.match_type is MatchType.FUZZY

    def test_exactly_one_percent_is_inclusive(self, evaluator: EvaluatorService) -> None:
        result = evaluator.score_field("amount", Decimal("1010.00"), Decimal("1000.00"))
        assert result.score == 0.9

    def test_outside_tolerance_scores_zero(self, evaluator: EvaluatorService) -> None:
        """Reading the subtotal instead of the total is a real error, not a typo."""
        result = evaluator.score_field("amount", Decimal("1100.00"), Decimal("1000.00"))
        assert result.score == 0.0
        assert result.match_type is MatchType.MISSING

    def test_zero_ground_truth_does_not_divide_by_zero(
        self, evaluator: EvaluatorService
    ) -> None:
        result = evaluator.score_field("amount", Decimal("5.00"), Decimal("0.00"))
        assert result.score == 0.0

    def test_unparseable_amount(self, evaluator: EvaluatorService) -> None:
        assert evaluator.score_field("amount", "illegible", Decimal("245.50")).score == 0.0

    def test_coerce_decimal_rejects_bool(self) -> None:
        assert coerce_decimal(True) is None

    def test_float_precision_is_not_lost(self) -> None:
        assert coerce_decimal(0.1) == Decimal("0.1")


# ---------------------------------------------------------------------------
# Nulls and hallucinations
# ---------------------------------------------------------------------------


class TestNullHandling:
    def test_both_null_scores_one(self, evaluator: EvaluatorService) -> None:
        result = evaluator.score_field("bill_number", None, None)
        assert result.score == 1.0
        assert result.match_type is MatchType.NOT_APPLICABLE

    @pytest.mark.parametrize("token", ["N/A", "null", "none", "-", "", "  ", "NIL", "not found"])
    def test_placeholder_strings_count_as_null(
        self, evaluator: EvaluatorService, token: str
    ) -> None:
        assert evaluator.score_field("bill_number", token, None).score == 1.0

    def test_model_null_when_value_exists_scores_zero(
        self, evaluator: EvaluatorService
    ) -> None:
        result = evaluator.score_field("bill_number", None, "INV-2024-001")
        assert result.score == 0.0
        assert result.match_type is MatchType.MISSING

    def test_hallucination_scores_zero_not_partial(
        self, evaluator: EvaluatorService
    ) -> None:
        """Inventing a bill number is the worst failure mode; no partial credit."""
        result = evaluator.score_field("bill_number", "INV-2024-001", None)
        assert result.score == 0.0
        assert result.match_type is MatchType.MISSING
        assert result.notes is not None and "Hallucination" in result.notes

    def test_is_null_helper(self) -> None:
        assert is_null(None) and is_null("  ") and is_null("N/A")
        assert not is_null("0") and not is_null(0)


# ---------------------------------------------------------------------------
# GSTIN
# ---------------------------------------------------------------------------


class TestTaxScoring:
    GSTIN = "07AABCU9603R1ZX"

    def test_matching_gstin(self, evaluator: EvaluatorService) -> None:
        result = evaluator.score_field(
            "tax_gst_details", f"GSTIN: {self.GSTIN}", f"GSTIN {self.GSTIN}"
        )
        assert result.score == 1.0
        assert result.match_type is MatchType.EXACT

    def test_one_wrong_character_gets_no_fuzzy_credit(
        self, evaluator: EvaluatorService
    ) -> None:
        """A GSTIN is an identifier, not prose -- 14/15 correct is still useless."""
        result = evaluator.score_field(
            "tax_gst_details", "GSTIN: 07AABCU9603R1ZY", f"GSTIN: {self.GSTIN}"
        )
        assert result.score == 0.0
        assert result.match_type is MatchType.MISSING

    def test_free_text_tax_falls_back_to_fuzzy(self, evaluator: EvaluatorService) -> None:
        result = evaluator.score_field(
            "tax_gst_details", "CGST 9% + SGST 9%", "CGST 9% + SGST 9%"
        )
        assert result.score == 1.0

    def test_extract_gstin_helper(self) -> None:
        assert extract_gstin(f"Tax {self.GSTIN} applied") == self.GSTIN
        assert extract_gstin("no tax id here") is None


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


class TestAggregation:
    GROUND_TRUTH = {
        "vendor_name": "Sharma General Store",
        "bill_number": None,
        "date": "2024-03-15",
        "amount": Decimal("245.50"),
        "currency": "INR",
        "tax_gst_details": None,
    }

    def test_perfect_extraction_scores_one(self, evaluator: EvaluatorService) -> None:
        scores = evaluator.evaluate(dict(self.GROUND_TRUTH), self.GROUND_TRUTH)
        assert evaluator.overall_accuracy(scores) == 1.0

    def test_evaluate_returns_every_field(self, evaluator: EvaluatorService) -> None:
        scores = evaluator.evaluate({}, self.GROUND_TRUTH)
        assert set(scores) == set(EXTRACTION_FIELDS)

    def test_partial_extraction_averages_correctly(
        self, evaluator: EvaluatorService
    ) -> None:
        predicted = {
            "vendor_name": "Sharma Genral Store",   # 0.9 fuzzy
            "bill_number": None,                    # 1.0 both null
            "date": "15/03/2024",                   # 1.0 same day
            "amount": Decimal("245.50"),            # 1.0 exact
            "currency": "INR",                      # 1.0 exact
            "tax_gst_details": None,                # 1.0 both null
        }
        scores = evaluator.evaluate(predicted, self.GROUND_TRUTH)
        assert evaluator.overall_accuracy(scores) == pytest.approx((0.9 + 5) / 6, abs=1e-4)

    def test_empty_scores_do_not_crash(self, evaluator: EvaluatorService) -> None:
        assert evaluator.overall_accuracy({}) == 0.0

    def test_dominant_match_type_breaks_ties_strictly(
        self, evaluator: EvaluatorService
    ) -> None:
        assert (
            evaluator.dominant_match_type([MatchType.EXACT, MatchType.MISSING])
            is MatchType.MISSING
        )
        assert (
            evaluator.dominant_match_type([MatchType.EXACT, MatchType.EXACT, MatchType.FUZZY])
            is MatchType.EXACT
        )

    def test_scored_field_serialises(self, evaluator: EvaluatorService) -> None:
        payload = evaluator.score_field("currency", "INR", "INR").to_dict()
        assert payload["field_name"] == "currency"
        assert payload["match_type"] == "exact"
        assert payload["score"] == 1.0


class TestNormalisation:
    def test_accents_are_folded(self) -> None:
        assert normalize_text("Café") == normalize_text("Cafe")

    def test_internal_punctuation_is_preserved(self) -> None:
        """Over-normalising would inflate every score; only edges are stripped."""
        assert normalize_text("S.G.Store") != normalize_text("SG Store")

    def test_none_becomes_empty_string(self) -> None:
        assert normalize_text(None) == ""


# ---------------------------------------------------------------------------
# Re-run deduplication
# ---------------------------------------------------------------------------


class TestLatestPerModel:
    """Extraction results are append-only, so re-runs must not double-count.

    A model run twice on one bill would otherwise get two columns in the
    comparison grid and two votes in the leaderboard average, silently
    weighting the benchmark toward whichever bill happened to be re-run.
    """

    class _Row:
        """Minimal stand-in for an ExtractionResult row."""

        def __init__(self, model_name: str, created_at: datetime, marker: str) -> None:
            self.model_name = model_name
            self.created_at = created_at
            self.marker = marker
            self.id = marker

    def _rows(self) -> list["TestLatestPerModel._Row"]:
        base = datetime(2026, 8, 2, 10, 0, 0)
        return [
            self._Row("groq-model", base, "first"),
            self._Row("groq-model", base + timedelta(minutes=5), "second"),
            self._Row("gemini-model", base + timedelta(minutes=5), "only"),
        ]

    def test_collapses_repeated_runs(self) -> None:
        from app.routers.evaluation import latest_per_model

        kept = latest_per_model(self._rows())  # type: ignore[arg-type]
        assert len(kept) == 2

    def test_keeps_the_newest_run(self) -> None:
        from app.routers.evaluation import latest_per_model

        kept = {r.model_name: r.marker for r in latest_per_model(self._rows())}  # type: ignore[arg-type]
        assert kept["groq-model"] == "second"

    def test_identical_timestamps_are_deterministic(self) -> None:
        """Two runs seconds apart can share a timestamp at second resolution."""
        from app.routers.evaluation import latest_per_model

        same = datetime(2026, 8, 2, 10, 0, 0)
        rows = [self._Row("m", same, "a"), self._Row("m", same, "b")]
        first = latest_per_model(rows)[0].marker  # type: ignore[arg-type]
        second = latest_per_model(list(reversed(rows)))[0].marker  # type: ignore[arg-type]
        assert first == second

    def test_empty_input(self) -> None:
        from app.routers.evaluation import latest_per_model

        assert latest_per_model([]) == []
