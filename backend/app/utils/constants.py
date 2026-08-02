"""Enums, prompts, pricing tables and scoring thresholds.

Everything a reviewer might want to tweak (the prompt, the fuzzy cut-offs, the
price per million tokens) lives here rather than being scattered through the
service layer.
"""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """``enum.StrEnum`` backport.

    Python 3.11 ships ``enum.StrEnum``; subclassing ``(str, Enum)`` gives the
    same JSON/SQL behaviour and keeps the module importable on 3.10, which is
    what several CI images still default to. Always read ``.value`` rather than
    relying on ``str()``, whose output differs between the two.
    """

    def __str__(self) -> str:
        return str(self.value)
from typing import Final, TypedDict

# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------


class BillStatus(StrEnum):
    """Lifecycle of an uploaded bill image."""

    UPLOADED = "uploaded"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ModelProvider(StrEnum):
    """Short slug the API accepts in ``{"models": [...]}``.

    The first three are native SDKs. The last three (Groq, SambaNova, Mistral)
    all expose an OpenAI-compatible ``/chat/completions`` endpoint, so they
    share one client implementation and differ only by base URL -- see
    :class:`app.services.llm_clients.OpenAICompatibleClient`.
    """

    GEMINI = "gemini"
    CLAUDE = "claude"
    OPENAI = "openai"
    GROQ = "groq"
    SAMBANOVA = "sambanova"
    MISTRAL = "mistral"
    NVIDIA = "nvidia"
    MOONSHOT = "moonshot"
    OPENROUTER = "openrouter"


class MatchType(StrEnum):
    """How a single extracted field compared to ground truth."""

    EXACT = "exact"
    FUZZY = "fuzzy"
    PARTIAL = "partial"
    MISSING = "missing"
    NOT_APPLICABLE = "not_applicable"


class SyncStatus(StrEnum):
    """State of a push to Zoho Books."""

    PENDING = "pending"
    SYNCED = "synced"
    FAILED = "failed"


# --------------------------------------------------------------------------
# The six fields under evaluation
# --------------------------------------------------------------------------

EXTRACTION_FIELDS: Final[tuple[str, ...]] = (
    "vendor_name",
    "bill_number",
    "date",
    "amount",
    "currency",
    "tax_gst_details",
)

TEXT_FIELDS: Final[frozenset[str]] = frozenset(
    {"vendor_name", "bill_number", "currency", "tax_gst_details"}
)

# --------------------------------------------------------------------------
# Scoring thresholds  (see docs/METHODOLOGY.md for the rationale)
# --------------------------------------------------------------------------

FUZZY_THRESHOLD: Final[int] = 90       # >= 90 -> 0.9, labelled "fuzzy"
PARTIAL_THRESHOLD: Final[int] = 70     # >= 70 -> 0.7, labelled "partial"
FUZZY_SCORE: Final[float] = 0.9
PARTIAL_SCORE: Final[float] = 0.7
EXACT_SCORE: Final[float] = 1.0
MISS_SCORE: Final[float] = 0.0

# An OCR'd handwritten "7" read as "1" in the paise column should not be scored
# the same as a wholly invented total, hence a tolerance band on amounts.
AMOUNT_TOLERANCE_PCT: Final[float] = 0.01   # 1 %
AMOUNT_NEAR_SCORE: Final[float] = 0.9

# --------------------------------------------------------------------------
# Master extraction prompt
# --------------------------------------------------------------------------

EXTRACTION_PROMPT: Final[str] = """\
You are an expert invoice parser specializing in Indian handwritten bills \
(kirana stores, chemists, auto-rickshaw receipts, small restaurants). These \
documents are informal: ballpoint on a rubber-stamped pad, photographed at an \
angle, sometimes in Devanagari or a regional script, often smudged.

OUTPUT CONTRACT -- these rules override everything else:
1. Return exactly ONE JSON object.
2. The FIRST character of your response MUST be {
3. The LAST character of your response MUST be }
4. Do NOT use markdown. Do NOT wrap the JSON in ``` or ```json fences.
5. Do NOT explain anything. No preamble, no reasoning, no commentary.
6. Do NOT write any text before the JSON or after the JSON.
7. Do NOT emit more than one JSON object and do NOT add extra closing braces.
8. If a value cannot be determined, return null for it. Never omit a key.
9. Never invent a value. A null is scored as an honest miss; a guess is scored
   as a wrong answer and is strictly worse.
10. Preserve the exact text found on the receipt wherever possible.

Return exactly this shape, with all six keys present:

{
  "vendor_name":     string | null,
  "bill_number":     string | null,
  "date":            string | null,
  "amount":          number | null,
  "currency":        string,
  "tax_gst_details": string | null
}

FIELD RULES:
- vendor_name: the shop or business name exactly as written. Do not expand
  abbreviations, do not correct spelling, do not append "Pvt Ltd".
- bill_number: the bill / invoice / receipt number as written. null if absent.
- date: ISO format "YYYY-MM-DD". Indian bills are written DD/MM/YY or
  DD-MM-YYYY -- read them day-first, never US month-first, then convert.
  Expand a 2-digit year to 20YY. If the year is genuinely absent, return null
  rather than assuming the current year.
- amount: the FINAL payable total, not a line item and not a subtotal. Strip
  currency symbols, commas and the word "Rupees". Return a bare number such as
  245.50.
- currency: ISO code. Default "INR" unless the bill clearly states otherwise.
- tax_gst_details: prefer a 15-character GSTIN if one is printed. Otherwise the
  tax line as written (e.g. "CGST 9% + SGST 9%"). null if the bill shows no tax
  information at all.

Respond now with the JSON object and nothing else.
"""

#: Sent as a corrective follow-up when the first reply could not be parsed.
#: Deliberately short and imperative -- a model that has just ignored a long
#: instruction block is not going to be persuaded by a longer one.
JSON_RETRY_INSTRUCTION: Final[str] = """\
You did not return valid JSON.
Return ONLY a valid JSON object.
Do not include explanations or markdown.
Start your response with { and end it with }.
"""

# --------------------------------------------------------------------------
# Pricing  (USD per 1,000,000 tokens)
# --------------------------------------------------------------------------


class ModelPrice(TypedDict):
    """Per-million-token USD rate for one model."""

    input_per_mtok: float
    output_per_mtok: float


# Verified against public price sheets in August 2026. Override per-model via
# the *_PRICE_IN / *_PRICE_OUT environment variables if a rate changes; the
# figures are only used for the cost-comparison column of the report.
MODEL_PRICING: Final[dict[str, ModelPrice]] = {
    # Google
    "gemini-2.5-flash": {"input_per_mtok": 0.30, "output_per_mtok": 2.50},
    "gemini-2.5-flash-lite": {"input_per_mtok": 0.10, "output_per_mtok": 0.40},
    "gemini-2.5-pro": {"input_per_mtok": 1.25, "output_per_mtok": 10.00},
    "gemini-3.5-flash": {"input_per_mtok": 1.50, "output_per_mtok": 9.00},
    # Anthropic
    "claude-haiku-4-5-20251001": {"input_per_mtok": 1.00, "output_per_mtok": 5.00},
    "claude-haiku-4-5": {"input_per_mtok": 1.00, "output_per_mtok": 5.00},
    # OpenAI
    "gpt-5-mini": {"input_per_mtok": 0.25, "output_per_mtok": 2.00},
    "gpt-5": {"input_per_mtok": 1.25, "output_per_mtok": 10.00},
    "gpt-4o-mini": {"input_per_mtok": 0.15, "output_per_mtok": 0.60},
    # Groq (OpenAI-compatible)
    "qwen/qwen3.6-27b": {"input_per_mtok": 0.60, "output_per_mtok": 3.00},
    # SambaNova (OpenAI-compatible)
    "Llama-4-Maverick-17B-128E-Instruct": {"input_per_mtok": 0.63, "output_per_mtok": 1.80},
    "Llama-4-Scout-17B-16E-Instruct": {"input_per_mtok": 0.40, "output_per_mtok": 0.70},
    # Mistral (OpenAI-compatible)
    "pixtral-12b-latest": {"input_per_mtok": 0.15, "output_per_mtok": 0.15},
    "pixtral-12b-2409": {"input_per_mtok": 0.15, "output_per_mtok": 0.15},
    "pixtral-large-latest": {"input_per_mtok": 2.00, "output_per_mtok": 6.00},
    "mistral-medium-latest": {"input_per_mtok": 0.40, "output_per_mtok": 2.00},
    # NVIDIA NIM (OpenAI-compatible). build.nvidia.com meters in credits rather
    # than publishing a per-token rate, so these are estimates from what the
    # same open weights cost elsewhere. Override with MODEL_PRICE_OVERRIDES to
    # make the cost column exact.
    "meta/llama-3.2-11b-vision-instruct": {"input_per_mtok": 0.06, "output_per_mtok": 0.06},
    "meta/llama-3.2-90b-vision-instruct": {"input_per_mtok": 0.35, "output_per_mtok": 0.40},
    # Moonshot / Kimi (OpenAI-compatible). k3 is confirmed at $3/$15; k2.6 is
    # an estimate from its position in the range -- override if it matters.
    "kimi-k3": {"input_per_mtok": 3.00, "output_per_mtok": 15.00},
    "kimi-k2.6": {"input_per_mtok": 0.60, "output_per_mtok": 2.50},
    # OpenRouter resells other labs' models and adds a small margin, so its
    # slugs need their own entries. Prices track the underlying provider.
    "moonshotai/kimi-k2.6": {"input_per_mtok": 0.60, "output_per_mtok": 2.50},
    "moonshotai/kimi-k2.6:free": {"input_per_mtok": 0.0, "output_per_mtok": 0.0},
    "moonshotai/kimi-k3": {"input_per_mtok": 3.00, "output_per_mtok": 15.00},
    "google/gemini-2.5-flash": {"input_per_mtok": 0.30, "output_per_mtok": 2.50},
    "qwen/qwen3.6-27b": {"input_per_mtok": 0.60, "output_per_mtok": 3.00},
    "meta-llama/llama-3.2-11b-vision-instruct": {
        "input_per_mtok": 0.06,
        "output_per_mtok": 0.06,
    },
}

# API keys carry recognisable prefixes. A key pointed at the wrong endpoint
# returns a bare 401, which sends people hunting for a typo in a key that is
# perfectly valid -- just for a different service. Used to turn that 401 into a
# specific diagnosis.
KEY_PREFIX_OWNERS: Final[tuple[tuple[str, str], ...]] = (
    ("sk-or-", "openrouter"),
    ("sk-ant-", "claude"),
    ("gsk_", "groq"),
    ("nvapi-", "nvidia"),
    ("AIza", "gemini"),
)

# Used when a model ID is not in the table above, so cost reporting degrades to
# an estimate rather than crashing.
FALLBACK_PRICE: Final[ModelPrice] = {"input_per_mtok": 0.50, "output_per_mtok": 2.00}

# Rough characters-per-token ratio for providers that do not return usage data.
CHARS_PER_TOKEN: Final[int] = 4

# A 1024x1024 image costs roughly this many tokens across the major vision
# models. Used only when the provider omits usage metadata.
IMAGE_TOKEN_ESTIMATE: Final[int] = 1_200

# --------------------------------------------------------------------------
# Image handling
# --------------------------------------------------------------------------

ALLOWED_IMAGE_EXTENSIONS: Final[frozenset[str]] = frozenset({".jpg", ".jpeg", ".png", ".webp"})

# Magic-number signatures -> canonical MIME type. Extension checks are trivially
# spoofable, so upload validation sniffs these bytes instead.
MAGIC_SIGNATURES: Final[tuple[tuple[bytes, str], ...]] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
)

WEBP_RIFF_PREFIX: Final[bytes] = b"RIFF"
WEBP_FORMAT_MARKER: Final[bytes] = b"WEBP"

EXTRAPOLATION_BILL_COUNT: Final[int] = 100
