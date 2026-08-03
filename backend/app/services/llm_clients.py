"""Unified async wrapper over the three vision LLM providers.

Each provider has a different SDK, a different image-encoding convention and a
different usage-metadata shape. :class:`UnifiedLLMClient` normalises all of it
so :mod:`app.services.extractors` can treat them interchangeably and the
evaluation stays apples-to-apples.

Model IDs are *not* hard-coded. They come from ``Settings`` (and therefore from
``.env``), because a vision model that exists today is a 404 in eighteen
months. The original brief specified gemini-1.5-flash, claude-3-haiku and
gpt-4o-mini; those generations are retired, so the defaults track their current
equivalents in the same price/latency tier. See ``docs/METHODOLOGY.md``.
"""

from __future__ import annotations

import abc
import base64
import binascii
import json
import logging
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Final

from app.config import Settings
from app.utils.constants import (
    CHARS_PER_TOKEN,
    EXTRACTION_PROMPT,
    FALLBACK_PRICE,
    JSON_RETRY_INSTRUCTION,
    IMAGE_TOKEN_ESTIMATE,
    KEY_PREFIX_OWNERS,
    MODEL_PRICING,
    ModelPrice,
    ModelProvider,
)
from app.utils.image_proc import load_image_for_llm

logger = logging.getLogger(__name__)

_FENCE_ANY = re.compile(r"```(?:json|JSON)?", re.IGNORECASE)
# Models sometimes emit Python literals or curly quotes inside JSON.
_PY_LITERALS = {"None": "null", "True": "true", "False": "false"}
_PY_LITERAL = re.compile(r"(?<![\w\"])(None|True|False)(?![\w\"])")
_SMART_QUOTE = re.compile(r"[\u201c\u201d]")
_TRAILING_COMMA = re.compile(r",\s*([}\]])")
# Reasoning models emit a chain of thought before the answer. Qwen, DeepSeek
# and friends all use <think>...</think>; an unterminated block means the
# model ran out of tokens mid-thought, so drop everything from the tag on.
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_UNCLOSED = re.compile(r"<think>.*\Z", re.DOTALL | re.IGNORECASE)

# Weak models ignore "return JSON" and answer in prose or markdown bullets:
#   * **Vendor Name**: Kmart
#   Vendor Name: Kmart
# The benchmark grades reading comprehension, not format compliance, so we
# recover the fields rather than scoring a correct read as a total miss.
# Horizontal whitespace only ([ \t], never \n): a plain \s* after the colon
# will happily cross a blank line, letting a header like "The receipt shows:"
# swallow the first real field on the line below it.
_PROSE_FIELD = re.compile(
    # Leading list marker: "-", "*", "+", ">", or "1." / "1)" -- NVIDIA's
    # Llama Vision answers in a numbered list, which is otherwise missed.
    r"^[ \t]*(?:[>*+\-]+|\d{1,2}[.)])?[ \t]*\**[ \t]*"
    r"([A-Za-z][A-Za-z /_-]{2,30}?)"
    r"[ \t]*\**[ \t]*[:\-][ \t]*"
    r"(\S[^\n]*?)[ \t]*$",
    re.MULTILINE,
)

#: Prose labels -> canonical field names.
_PROSE_ALIASES: Final[dict[str, str]] = {
    "vendor name": "vendor_name",
    "vendor": "vendor_name",
    "shop name": "vendor_name",
    "merchant": "vendor_name",
    "bill number": "bill_number",
    "invoice number": "bill_number",
    "receipt number": "bill_number",
    "bill no": "bill_number",
    "date": "date",
    "bill date": "date",
    "invoice date": "date",
    "amount": "amount",
    "total": "amount",
    "total amount": "amount",
    "grand total": "amount",
    "currency": "currency",
    "tax gst details": "tax_gst_details",
    "tax/gst details": "tax_gst_details",
    "tax details": "tax_gst_details",
    "gst": "tax_gst_details",
    "tax": "tax_gst_details",
}

_PROSE_NULLS: Final[frozenset[str]] = frozenset(
    {"none", "null", "n/a", "na", "not provided", "not available", "not specified",
     "not found", "unknown", "-", "not mentioned", "nil"}
)


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class LLMError(RuntimeError):
    """Base class for every provider failure."""


class LLMConfigurationError(LLMError):
    """The provider is missing an API key or its SDK is not installed."""


class LLMInvocationError(LLMError):
    """The provider was reachable but the call failed (auth, quota, timeout)."""


class LLMResponseError(LLMError):
    """The call succeeded but the body was not usable JSON."""


#: Substrings that mark an error as "this key is out of budget" rather than
#: "this request was wrong". Providers spell it several ways -- Google returns
#: RESOURCE_EXHAUSTED, most OpenAI-compatible services return a 429 -- and none
#: of them expose a machine-readable code through the SDK exception, so the
#: message text is what there is to go on.
_QUOTA_MARKERS: Final[tuple[str, ...]] = (
    "resource_exhausted",
    "resource exhausted",
    "quota",
    "rate limit",
    "rate_limit",
    "ratelimit",
    "too many requests",
    "429",
)


def is_quota_error(message: str) -> bool:
    """True when a provider error reads as an exhausted key rather than a bug.

    Deliberately generous: a false positive costs one extra call on a standby
    key, while a false negative aborts the run the standby exists to rescue.
    """
    lowered = message.lower()
    return any(marker in lowered for marker in _QUOTA_MARKERS)


# --------------------------------------------------------------------------
# Result container
# --------------------------------------------------------------------------


@dataclass(slots=True)
class ExtractionOutcome:
    """Everything one model produced for one bill, success or failure."""

    provider: str
    model_name: str
    fields: dict[str, Any] = field(default_factory=dict)
    raw_response: str = ""
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    token_source: str = "estimated"
    succeeded: bool = True
    error_message: str | None = None
    #: True when the first reply was unparseable and a corrective re-prompt was
    #: needed. Tracked so the cost/latency figures stay honest -- a retry is a
    #: second billed call -- and so unreliable formatters are visible in logs.
    retried: bool = False


# --------------------------------------------------------------------------
# Base class
# --------------------------------------------------------------------------


class UnifiedLLMClient(abc.ABC):
    """Provider-agnostic contract for a vision extraction call."""

    provider: ModelProvider

    #: Ceiling on the base64 image payload, when the provider imposes one.
    #: ``None`` means "no provider-side limit"; the image is still downscaled
    #: for cost, just not compressed to a hard budget.
    max_image_b64_bytes: int | None = None

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        settings: Settings,
        fallback_api_keys: list[str] | None = None,
    ) -> None:
        if not api_key or not api_key.strip():
            raise LLMConfigurationError(
                f"No API key configured for provider '{self.provider.value}'."
            )
        self._api_key = api_key.strip()
        #: Standby keys for the same provider, tried in order when the primary
        #: is refused for quota reasons. Empty for every provider that does not
        #: opt in -- see :func:`build_client`.
        self._fallback_api_keys = [
            key.strip() for key in (fallback_api_keys or []) if key and key.strip()
        ]
        self._model_name = model_name
        self._settings = settings

    # ------------------------------------------------------------- contract
    def get_model_name(self) -> str:
        """Return the exact provider-side model identifier being billed."""
        return self._model_name

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """USD cost of a call, from the published per-million-token rates.

        Falls back to a mid-range rate for unknown model IDs so that swapping in
        a brand-new model degrades the cost column to an estimate instead of
        raising.
        """
        # OpenRouter exposes free variants as "<model>:free". They are billed at
        # zero, so hard-coding a price entry per variant would be both wrong and
        # endless. Note the trade being measured: free variants carry tighter
        # rate limits, so they cost latency and throughput rather than money.
        if self._model_name.endswith(":free"):
            return 0.0

        override = self._settings.model_price_overrides.get(self._model_name)
        if override and len(override) == 2:
            price: ModelPrice = {
                "input_per_mtok": float(override[0]),
                "output_per_mtok": float(override[1]),
            }
        else:
            price = MODEL_PRICING.get(self._model_name)  # type: ignore[assignment]
        if price is None:
            price = MODEL_PRICING.get(self._model_name.rsplit("-", 1)[0], FALLBACK_PRICE)
            logger.debug("No price entry for %s; using fallback rate.", self._model_name)
        cost = (input_tokens / 1_000_000) * price["input_per_mtok"] + (
            output_tokens / 1_000_000
        ) * price["output_per_mtok"]
        return round(cost, 8)

    @abc.abstractmethod
    async def _invoke(
        self, image_b64: str, mime_type: str, *, extra_instruction: str | None = None
    ) -> tuple[str, int | None, int | None]:
        """Call the provider.

        Args:
            image_b64: Base64-encoded image, already sized for this provider.
            mime_type: MIME type matching ``image_b64``.
            extra_instruction: Appended to the prompt on a corrective retry.

        Returns:
            ``(response_text, input_tokens_or_None, output_tokens_or_None)``.
            ``None`` token counts mean the provider returned no usage metadata
            and the caller should fall back to estimation.
        """

    def _prompt(self, extra_instruction: str | None = None) -> str:
        """The extraction prompt, with any corrective instruction appended."""
        if not extra_instruction:
            return EXTRACTION_PROMPT
        return f"{EXTRACTION_PROMPT}\n\n{extra_instruction}"

    # ----------------------------------------------------------- public API
    async def extract_bill_data(self, image_path: str) -> ExtractionOutcome:
        """Run one extraction and always return an :class:`ExtractionOutcome`.

        This method never raises. A provider outage, a quota wall or a model
        that returns prose instead of JSON all come back as
        ``succeeded=False`` with a human-readable ``error_message``, so one bad
        provider cannot abort a three-way comparison.
        """
        outcome = ExtractionOutcome(
            provider=self.provider.value, model_name=self.get_model_name()
        )
        started = time.perf_counter()
        try:
            image_b64, mime_type = load_image_for_llm(
                image_path, max_encoded_bytes=self.max_image_b64_bytes
            )
            text, in_tok, out_tok = await self._invoke(image_b64, mime_type)
            self._record_usage(outcome, text, in_tok, out_tok, image_b64)

            try:
                outcome.fields = parse_llm_json(outcome.raw_response)
            except LLMResponseError as first_failure:
                # One corrective re-prompt. Models that ignore a format
                # instruction usually comply when told plainly that they broke
                # it; a second failure means the model genuinely cannot do it,
                # and further retries just spend money to learn the same thing.
                logger.info(
                    "%s returned unparseable output (%s); re-prompting once for JSON.",
                    self.get_model_name(),
                    first_failure,
                )
                outcome.retried = True
                retry_text, retry_in, retry_out = await self._invoke(
                    image_b64, mime_type, extra_instruction=JSON_RETRY_INSTRUCTION
                )
                self._record_usage(
                    outcome, retry_text, retry_in, retry_out, image_b64, accumulate=True
                )
                outcome.fields = parse_llm_json(retry_text or "")

            outcome.latency_ms = int((time.perf_counter() - started) * 1000)
            outcome.succeeded = True

        except LLMError as exc:
            outcome.latency_ms = int((time.perf_counter() - started) * 1000)
            outcome.succeeded = False
            outcome.error_message = str(exc)
            logger.warning("%s extraction failed: %s", self.get_model_name(), exc)
        except Exception as exc:  # noqa: BLE001 - last line of defence
            outcome.latency_ms = int((time.perf_counter() - started) * 1000)
            outcome.succeeded = False
            outcome.error_message = f"Unexpected {type(exc).__name__}: {exc}"
            logger.exception("Unexpected failure in %s client.", self.get_model_name())

        return outcome

    # ------------------------------------------------------------- internal
    def _record_usage(
        self,
        outcome: ExtractionOutcome,
        text: str | None,
        in_tok: int | None,
        out_tok: int | None,
        image_b64: str,
        *,
        accumulate: bool = False,
    ) -> None:
        """Fold one call's response and usage into the outcome.

        ``accumulate=True`` adds to the running totals instead of replacing
        them, so a retried extraction reports the cost of *both* calls. Hiding
        the retry's cost would make an unreliable model look cheaper than a
        well-behaved one, which is the opposite of what the benchmark is for.
        """
        body = text or ""
        if accumulate:
            outcome.raw_response = f"{outcome.raw_response}\n\n--- retry ---\n\n{body}"
        else:
            outcome.raw_response = body

        if in_tok is not None and out_tok is not None:
            call_in, call_out, source = in_tok, out_tok, "provider"
        else:
            call_in = self._estimate_input_tokens(image_b64)
            call_out = max(1, len(body) // CHARS_PER_TOKEN)
            source = "estimated"

        if accumulate:
            outcome.input_tokens += call_in
            outcome.output_tokens += call_out
            # A mixed pair is only as trustworthy as its weaker half.
            if source == "estimated":
                outcome.token_source = "estimated"
        else:
            outcome.input_tokens, outcome.output_tokens = call_in, call_out
            outcome.token_source = source

        outcome.cost_usd = self.estimate_cost(outcome.input_tokens, outcome.output_tokens)

    @staticmethod
    def _estimate_input_tokens(image_b64: str) -> int:
        """Approximate prompt tokens when the provider reports no usage.

        Base64 inflates bytes by 4/3, so we recover the raw byte count before
        applying the flat per-image estimate.
        """
        prompt_tokens = len(EXTRACTION_PROMPT) // CHARS_PER_TOKEN
        raw_bytes = int(len(image_b64) * 0.75)
        image_tokens = max(IMAGE_TOKEN_ESTIMATE, raw_bytes // 750)
        return prompt_tokens + image_tokens

    @staticmethod
    def _decode(image_b64: str) -> bytes:
        try:
            return base64.b64decode(image_b64, validate=True)
        except (binascii.Error, ValueError) as exc:  # pragma: no cover - defensive
            raise LLMInvocationError(f"Could not decode prepared image: {exc}") from exc


# --------------------------------------------------------------------------
# JSON repair
# --------------------------------------------------------------------------


def strip_code_fences(text: str) -> str:
    """Remove markdown fences and surrounding whitespace.

    Handles both ```` ```json ```` and bare ```` ``` ````, opening and closing,
    wherever they appear -- some models fence only the opening.
    """
    return _FENCE_ANY.sub("", text).strip()


def strip_reasoning(text: str) -> str:
    """Drop ``<think>`` chain-of-thought blocks, closed or truncated."""
    return _THINK_UNCLOSED.sub("", _THINK_BLOCK.sub("", text))


def iter_json_objects(text: str) -> Iterator[str]:
    """Yield every complete, brace-balanced JSON object in ``text``, in order.

    This is the core of the parser and the reason it survives real model output.
    A naive ``text[find("{") : rfind("}") + 1]`` slice breaks on the two most
    common malformations at once:

    * ``{...}\n}`` -- a stray trailing brace. ``rfind`` grabs the stray one and
      produces an unbalanced string.
    * ``Here you go: {...} Hope that helps!`` -- prose on both sides.

    Scanning with a depth counter that respects string literals and escapes
    yields the *first complete object* regardless of what surrounds it, so
    leading prose, trailing prose and extra braces are all simply not part of
    the match.
    """
    depth = 0
    start: int | None = None
    in_string = False
    escaped = False

    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                yield text[start : index + 1]
                start = None


def repair_json(fragment: str) -> str:
    """Fix the malformations models produce that ``json.loads`` rejects.

    Deliberately conservative: every rule here targets a syntax error, never a
    value. Nothing in this function can change what the model actually read.
    """
    repaired = _TRAILING_COMMA.sub(r"\1", fragment)
    repaired = _PY_LITERAL.sub(lambda m: _PY_LITERALS[m.group(0)], repaired)
    repaired = _SMART_QUOTE.sub('"', repaired)
    return repaired


def close_truncated_json(fragment: str) -> str | None:
    """Best-effort repair of JSON cut off by the output-token limit.

    A model that runs out of budget mid-object leaves something like
    ``{"a": 1, "b": null`` -- structurally invalid, but every field it *did*
    emit is good. Discarding all of it because the closing brace is missing
    would score a mostly-correct extraction as a total failure.

    Walks the fragment tracking string state and bracket depth, drops any
    trailing partial key/value, and appends the missing closers. Returns
    ``None`` when there is nothing to close.
    """
    depth: list[str] = []
    in_string = False
    escaped = False
    last_safe = 0  # index just past the last completed key/value pair

    for index, char in enumerate(fragment):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            depth.append("}" if char == "{" else "]")
        elif char in "}]":
            if depth:
                depth.pop()
            last_safe = index + 1
        elif char == ",":
            last_safe = index  # cut here; the comma itself is dropped

    if not depth and not in_string:
        return None

    # An unterminated string means the final value is partial -- rewind to the
    # last complete pair rather than inventing a closing quote.
    body = fragment[:last_safe] if in_string else fragment.rstrip().rstrip(",")
    if not body.strip():
        return None
    return body + "".join(reversed(depth))


def parse_prose_fields(text: str) -> dict[str, Any]:
    """Recover the six fields from a prose or markdown-bullet answer.

    Small models routinely ignore JSON instructions and reply with
    ``* **Vendor Name**: Kmart``. That is a *successful read* expressed in the
    wrong container. Since the rubric already refuses to penalise packaging
    (fences, trailing commas), it would be inconsistent to score this as a miss.

    Returns ``{}`` when nothing recognisable is found, so the caller can still
    fail loudly.
    """
    found: dict[str, Any] = {}
    for label, value in _PROSE_FIELD.findall(text):
        key = _PROSE_ALIASES.get(label.strip().lower().replace("_", " "))
        if key is None or key in found:
            continue
        cleaned = value.strip().strip("*").strip().rstrip(".")
        if cleaned.lower() in _PROSE_NULLS:
            found[key] = None
        else:
            found[key] = cleaned
    return found


def _as_field_dict(parsed: Any) -> dict[str, Any] | None:
    """Normalise a parsed JSON value into a field dict, or ``None``."""
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
        # Some models wrap the object in a single-element array.
        return parsed[0]
    return None


def parse_llm_json(text: str) -> dict[str, Any]:
    """Coerce a model's reply into a field dict, tolerating real-world output.

    The pipeline, in order of preference:

    1. Strip reasoning blocks and markdown fences.
    2. Parse the whole thing -- the happy path, no repair needed.
    3. Walk every brace-balanced object in the text and take the first that
       parses. This alone handles leading prose, trailing prose and stray
       extra braces.
    4. Re-try each candidate through :func:`repair_json` (trailing commas,
       Python literals, smart quotes).
    5. If the output was truncated, close the open braces and parse that.
    6. As a last resort, recover fields from prose.

    Raises:
        LLMResponseError: If no JSON object can be recovered at all. The caller
            is expected to respond by re-prompting once -- see
            :meth:`UnifiedLLMClient.extract_bill_data`.
    """
    if not text or not text.strip():
        raise LLMResponseError("Model returned an empty response.")

    cleaned = strip_code_fences(strip_reasoning(text))

    # 2. Whole-response parse.
    try:
        if (fields := _as_field_dict(json.loads(cleaned))) is not None:
            return fields
    except (json.JSONDecodeError, ValueError):
        logger.debug("Whole-response parse failed; falling through to repair.")

    # 3 + 4. Every balanced object, raw then repaired.
    candidates = list(iter_json_objects(cleaned))
    for candidate in candidates:
        for attempt in (candidate, repair_json(candidate)):
            try:
                if (fields := _as_field_dict(json.loads(attempt))) is not None:
                    return fields
            except (json.JSONDecodeError, ValueError):
                continue

    # 5. Truncated output: close what is open.
    opening = cleaned.find("{")
    if opening != -1 and (closed := close_truncated_json(cleaned[opening:])) is not None:
        for attempt in (closed, repair_json(closed)):
            try:
                if (fields := _as_field_dict(json.loads(attempt))) is not None:
                    return fields
            except (json.JSONDecodeError, ValueError):
                continue

    # 6. Prose fallback -- a correct read in the wrong container still counts.
    if prose := parse_prose_fields(text):
        logger.info("Recovered %d field(s) from a prose response.", len(prose))
        return prose

    preview = text.strip()[:300]
    if "<think>" in text.lower():
        raise LLMResponseError(
            "Model spent its whole token budget reasoning and never emitted JSON. "
            "Disable thinking (Groq: reasoning_effort=none) or raise "
            f"LLM_MAX_OUTPUT_TOKENS. First 300 chars: {preview!r}"
        )
    raise LLMResponseError(f"Response was not valid JSON. First 300 chars: {preview!r}")


# --------------------------------------------------------------------------
# Google Gemini
# --------------------------------------------------------------------------


class GeminiClient(UnifiedLLMClient):
    """Google Gemini via the ``google-genai`` SDK.

    Chosen as the cost floor of the comparison: Gemini Flash has a genuinely
    free tier (no card required), which makes this project reproducible by a
    reviewer who will not pay to run someone else's take-home.
    """

    provider = ModelProvider.GEMINI

    def _thinking_configs(self, types: Any) -> list[dict[str, Any]]:
        """Thinking settings to try, most specific first.

        Gemini 3.x takes ``thinking_level``; 2.x takes ``thinking_budget``.
        Rather than branch on a model-name regex -- which breaks on the next
        naming change -- we try both and fall through to no config at all.
        """
        level = self._settings.gemini_thinking_level.strip()
        if not level:
            return [{}]

        attempts: list[dict[str, Any]] = []
        try:
            attempts.append(
                {"thinking_config": types.ThinkingConfig(thinking_level=level)}
            )
        except Exception as exc:  # noqa: BLE001 - older SDKs have no thinking_level
            logger.debug("SDK does not accept thinking_level (%s); skipping it.", exc)
        try:
            attempts.append({"thinking_config": types.ThinkingConfig(thinking_budget=0)})
        except Exception as exc:  # noqa: BLE001 - newer SDKs may drop thinking_budget
            logger.debug("SDK does not accept thinking_budget (%s); skipping it.", exc)
        attempts.append({})
        return attempts

    async def _invoke(
        self, image_b64: str, mime_type: str, *, extra_instruction: str | None = None
    ) -> tuple[str, int | None, int | None]:
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:  # pragma: no cover
            raise LLMConfigurationError(
                "google-genai is not installed. Run: pip install -r requirements.txt"
            ) from exc

        contents = [
            types.Part.from_bytes(data=self._decode(image_b64), mime_type=mime_type),
            self._prompt(extra_instruction),
        ]

        # Primary key first, standbys after. Only a quota refusal advances to
        # the next key: a bad model ID or a malformed image will fail
        # identically on every key, so retrying those just burns the standby's
        # allowance to reach the same error.
        keys = [self._api_key, *self._fallback_api_keys]
        for index, api_key in enumerate(keys):
            try:
                return await self._invoke_with_key(genai, types, api_key, contents)
            except LLMInvocationError as exc:
                is_last = index + 1 >= len(keys)
                if is_last or not is_quota_error(str(exc)):
                    raise
                logger.warning(
                    "Gemini key %d of %d is out of quota (%s); switching to the "
                    "next configured key.",
                    index + 1,
                    len(keys),
                    exc,
                )

        # Unreachable: __init__ guarantees a non-empty primary key, so the loop
        # above always either returns or raises.
        raise LLMInvocationError(  # pragma: no cover - defensive
            "No Gemini API key was available to call."
        )

    async def _invoke_with_key(
        self, genai: Any, types: Any, api_key: str, contents: list[Any]
    ) -> tuple[str, int | None, int | None]:
        """Run one full generate_content attempt against a single API key."""
        client = genai.Client(api_key=api_key)

        # Gemini 2.5+ thinks by default, and thinking tokens are charged against
        # max_output_tokens -- so the model can reason itself out of budget and
        # return JSON truncated mid-object. Transcription needs no reasoning, so
        # we turn it down. The parameter was renamed between generations
        # (thinking_budget -> thinking_level), hence the ladder: each config is
        # tried in turn and the first the SDK accepts wins.
        for thinking in self._thinking_configs(types):
            config = types.GenerateContentConfig(
                temperature=0.0,
                max_output_tokens=self._settings.llm_max_output_tokens,
                response_mime_type="application/json",
                **thinking,
            )
            try:
                response = await client.aio.models.generate_content(
                    model=self._model_name, contents=contents, config=config
                )
                break
            except TypeError:
                continue  # SDK rejected the kwarg; try the next spelling
            except Exception as exc:  # noqa: BLE001 - SDK raises a wide family
                message = str(exc)
                if "thinking" in message.lower() and thinking:
                    logger.info("Gemini rejected %s; retrying without it.", list(thinking))
                    continue
                raise LLMInvocationError(f"Gemini call failed: {exc}") from exc
        else:  # pragma: no cover - the ladder always ends with an empty config
            raise LLMInvocationError("Gemini rejected every thinking configuration.")

        usage = getattr(response, "usage_metadata", None)
        in_tok = getattr(usage, "prompt_token_count", None) if usage else None
        out_tok = getattr(usage, "candidates_token_count", None) if usage else None
        return (response.text or "", in_tok, out_tok)


# --------------------------------------------------------------------------
# Anthropic Claude
# --------------------------------------------------------------------------


class ClaudeClient(UnifiedLLMClient):
    """Anthropic Claude via the official async SDK.

    Included as the accuracy reference point. In the small-sample runs this
    project is built for, Claude is the model most willing to return ``null``
    on an illegible field instead of guessing -- which the rubric rewards.
    """

    provider = ModelProvider.CLAUDE

    async def _invoke(
        self, image_b64: str, mime_type: str, *, extra_instruction: str | None = None
    ) -> tuple[str, int | None, int | None]:
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:  # pragma: no cover
            raise LLMConfigurationError(
                "anthropic is not installed. Run: pip install -r requirements.txt"
            ) from exc

        client = AsyncAnthropic(api_key=self._api_key, timeout=self._settings.llm_timeout_seconds)
        try:
            message = await client.messages.create(
                model=self._model_name,
                max_tokens=self._settings.llm_max_output_tokens,
                temperature=0.0,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": mime_type,
                                    "data": image_b64,
                                },
                            },
                            {"type": "text", "text": self._prompt(extra_instruction)},
                        ],
                    }
                ],
            )
        except Exception as exc:  # noqa: BLE001
            raise LLMInvocationError(f"Anthropic call failed: {exc}") from exc
        finally:
            await client.close()

        text = "".join(
            block.text for block in message.content if getattr(block, "type", None) == "text"
        )
        usage = getattr(message, "usage", None)
        in_tok = getattr(usage, "input_tokens", None) if usage else None
        out_tok = getattr(usage, "output_tokens", None) if usage else None
        return (text, in_tok, out_tok)


# --------------------------------------------------------------------------
# OpenAI-compatible providers
#
# OpenAI, Groq, SambaNova and Mistral all expose the same
# POST /chat/completions contract with the same multimodal message shape.
# Rather than write four near-identical clients, there is one implementation
# parameterised by base URL -- adding a fifth OpenAI-compatible provider is a
# subclass with two class attributes.
# --------------------------------------------------------------------------


class OpenAICompatibleClient(UnifiedLLMClient):
    """Vision extraction over any OpenAI-compatible ``/chat/completions``.

    The image is sent as a base64 data URL in an ``image_url`` content block,
    which every provider in this family accepts. ``response_format`` constrains
    the decoder to emit a JSON object rather than merely asking the model to in
    the prompt.
    """

    #: Endpoint root. ``None`` means "use the SDK default" (api.openai.com).
    base_url: str | None = None
    #: ``max_completion_tokens`` on newer OpenAI-style APIs, ``max_tokens`` on
    #: the rest. A mismatch is recovered from automatically -- see ``_invoke``.
    token_param: str = "max_completion_tokens"
    #: Whether to request server-side JSON mode.
    supports_json_mode: bool = True
    #: Whether the provider accepts ``detail: high`` on image blocks.
    supports_image_detail: bool = True

    def extra_params(self) -> dict[str, Any]:
        """Provider-specific body fields merged into every request.

        Kept separate from ``_build_payload`` so ``_degrade`` can drop them
        wholesale when a provider rejects one: these are all optional
        optimisations, and a request without them still works.
        """
        return {}

    def default_headers(self) -> dict[str, str]:
        """Extra HTTP headers this provider wants on every request."""
        return {}

    def diagnose_auth_failure(self) -> str:
        """Explain a 401 in terms of *which* service the key belongs to.

        A bare "Invalid Authentication" is the least useful error in this
        project: the key is usually valid, just pointed at the wrong endpoint.
        Because API keys carry recognisable prefixes we can name the mismatch
        outright instead of leaving the user to re-check a correct key.
        """
        for prefix, owner in KEY_PREFIX_OWNERS:
            if self._api_key.startswith(prefix) and owner != self.provider.value:
                return (
                    f" This key starts with '{prefix}', which is a {owner} key, but it "
                    f"was sent to {self.provider.value} at {self.base_url}. Either set "
                    f"{owner.upper()}_API_KEY instead, or point "
                    f"{self.provider.value.upper()}_BASE_URL at the right service."
                )
        return (
            f" Check {self.provider.value.upper()}_API_KEY is a key issued by "
            f"{self.base_url}, and that the account has credit."
        )

    def _build_payload(
        self, image_b64: str, mime_type: str, extra_instruction: str | None = None
    ) -> dict[str, Any]:
        """Assemble the request body for this provider."""
        image_block: dict[str, Any] = {"url": f"data:{mime_type};base64,{image_b64}"}
        if self.supports_image_detail:
            image_block["detail"] = "high"

        payload: dict[str, Any] = {
            "model": self._model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": self._prompt(extra_instruction)},
                        {"type": "image_url", "image_url": image_block},
                    ],
                }
            ],
            self.token_param: self._settings.llm_max_output_tokens,
        }
        if self.supports_json_mode:
            payload["response_format"] = {"type": "json_object"}

        # Non-standard fields must travel via extra_body: the OpenAI SDK
        # validates its keyword arguments and raises TypeError on anything it
        # does not recognise, whereas extra_body is merged verbatim into the
        # request JSON. That is exactly what vendor extensions like Groq's
        # reasoning_format need.
        if extras := self.extra_params():
            payload["extra_body"] = extras
        return payload

    async def _invoke(
        self, image_b64: str, mime_type: str, *, extra_instruction: str | None = None
    ) -> tuple[str, int | None, int | None]:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover
            raise LLMConfigurationError(
                "openai is not installed. Run: pip install -r requirements.txt"
            ) from exc

        client = AsyncOpenAI(
            api_key=self._api_key,
            base_url=self.base_url,
            timeout=self._settings.llm_timeout_seconds,
            max_retries=1,
            default_headers=self.default_headers() or None,
        )
        payload = self._build_payload(image_b64, mime_type, extra_instruction)

        try:
            try:
                response = await client.chat.completions.create(**payload)
            except Exception as exc:  # noqa: BLE001
                message = str(exc)
                if "401" in message or "authentication" in message.lower():
                    raise LLMInvocationError(
                        f"{self.provider.value} rejected the API key (401)."
                        f"{self.diagnose_auth_failure()}"
                    ) from exc

                fallback = self._degrade(payload, message)
                if fallback is None:
                    raise LLMInvocationError(
                        f"{self.provider.value} call failed: {exc}"
                    ) from exc
                logger.info(
                    "%s rejected part of the request (%s); retrying with a reduced payload.",
                    self.provider.value,
                    type(exc).__name__,
                )
                try:
                    response = await client.chat.completions.create(**fallback)
                except Exception as retry_exc:  # noqa: BLE001
                    raise LLMInvocationError(
                        f"{self.provider.value} call failed: {retry_exc}"
                    ) from retry_exc
        finally:
            await client.close()

        text = response.choices[0].message.content or "" if response.choices else ""
        usage = getattr(response, "usage", None)
        in_tok = getattr(usage, "prompt_tokens", None) if usage else None
        out_tok = getattr(usage, "completion_tokens", None) if usage else None
        return (text, in_tok, out_tok)

    def _degrade(self, payload: dict[str, Any], message: str) -> dict[str, Any] | None:
        """Build a reduced payload for one retry, or ``None`` if the error is fatal.

        Providers in this family disagree on two optional details: the name of
        the output-token cap, and whether they support JSON mode. Both produce a
        400 that names the offending parameter, so rather than pinning the user
        to one provider generation we drop the offending field and try once
        more. Errors that are not about a parameter (auth, quota, model not
        found) return ``None`` so they surface immediately.
        """
        lowered = message.lower()
        reduced = dict(payload)
        changed = False

        # Reasoning controls are provider- and model-specific. Switching
        # GROQ_MODEL to a non-Qwen model, for instance, makes reasoning_effort
        # invalid. Drop them and let the prompt carry the load.
        if "reasoning" in lowered:
            extra_body = dict(reduced.get("extra_body") or {})
            for key in ("reasoning_effort", "reasoning_format", "include_reasoning"):
                if extra_body.pop(key, None) is not None:
                    changed = True
            if changed:
                if extra_body:
                    reduced["extra_body"] = extra_body
                else:
                    reduced.pop("extra_body", None)

        # Groq returns json_validate_failed when a model produces nothing that
        # satisfies JSON mode -- usually because reasoning consumed the whole
        # budget. Retrying without the decoder constraint gets us raw text that
        # parse_llm_json can still salvage.
        if "json_validate_failed" in lowered or "failed to validate json" in lowered:
            reduced.pop("response_format", None)
            reduced.pop("extra_body", None)
            changed = True

        if "max_completion_tokens" in lowered or "max_tokens" in lowered:
            other = (
                "max_tokens"
                if self.token_param == "max_completion_tokens"
                else "max_completion_tokens"
            )
            reduced.pop(self.token_param, None)
            reduced[other] = self._settings.llm_max_output_tokens
            changed = True

        if "response_format" in lowered or "json_object" in lowered or "json mode" in lowered:
            reduced.pop("response_format", None)
            changed = True

        if "detail" in lowered and "image" in lowered:
            for block in reduced["messages"][0]["content"]:
                if block.get("type") == "image_url":
                    block["image_url"].pop("detail", None)
            changed = True

        return reduced if changed else None


class OpenAIClient(OpenAICompatibleClient):
    """OpenAI's own hosted models.

    Included as the ecosystem baseline: it is the default most teams reach for
    first, so it is the number the others have to beat.
    """

    provider = ModelProvider.OPENAI

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.base_url = self._settings.openai_base_url


class GroqClient(OpenAICompatibleClient):
    """Groq's LPU-hosted open-weight vision models.

    Included as the latency reference. Groq's whole proposition is inference
    speed, and the report's ``avg_latency_ms`` column is where that shows up --
    on open weights that also happen to be cheap.
    """

    provider = ModelProvider.GROQ
    # Groq's docs use max_completion_tokens and demonstrate JSON mode with an
    # image input, so both are enabled.
    token_param = "max_completion_tokens"
    supports_json_mode = True
    # Groq's vision endpoint ignores `detail`; omitted to keep requests clean.
    supports_image_detail = False

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.base_url = self._settings.groq_base_url

    def extra_params(self) -> dict[str, Any]:
        """Turn off chain-of-thought reasoning.

        Groq's current vision model, Qwen 3.6 27B, is a *reasoning* model. Left
        alone it does three unhelpful things here:

        * ``reasoning_format`` defaults to ``raw``, so the reply arrives wrapped
          in ``<think>...</think>`` rather than as bare JSON;
        * the chain of thought consumes the output budget, and on a dense bill
          it can exhaust all of it before a single field is emitted -- which
          Groq surfaces as ``json_validate_failed`` with an empty generation;
        * you pay for every one of those tokens.

        Field extraction is transcription, not puzzle-solving, so there is
        nothing to reason about. ``reasoning_effort="none"`` (Qwen-only) removes
        the chain entirely, and ``reasoning_format="hidden"`` keeps any residue
        out of the content -- ``raw`` is rejected outright alongside JSON mode.
        Both are dropped automatically by ``_degrade`` if a different Groq model
        rejects them.
        """
        params: dict[str, Any] = {}
        if effort := self._settings.groq_reasoning_effort.strip():
            params["reasoning_effort"] = effort
            params["reasoning_format"] = "hidden"
        return params


class SambaNovaClient(OpenAICompatibleClient):
    """SambaNova Cloud, running open-weight multimodal models on RDU hardware.

    Included as a second open-weights datapoint. Comparing it against Groq on
    the same model family separates *model* quality from *serving* quality,
    which a single-provider benchmark cannot do.

    Note: SambaNova accepts base64 data URLs only -- it does not fetch remote
    image URLs. That suits us; we always send base64.
    """

    provider = ModelProvider.SAMBANOVA
    token_param = "max_tokens"
    supports_json_mode = True
    supports_image_detail = False

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.base_url = self._settings.sambanova_base_url


class MistralClient(OpenAICompatibleClient):
    """Mistral's Pixtral multimodal family via the OpenAI-compatible endpoint.

    Included because Pixtral was trained with document understanding as a
    first-class target rather than as a side effect of general vision, which
    makes it a genuinely different bet on this task. Uses the compatibility
    endpoint rather than ``mistralai`` so it shares this client and adds no
    dependency.
    """

    provider = ModelProvider.MISTRAL
    token_param = "max_tokens"
    supports_json_mode = True
    supports_image_detail = False

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.base_url = self._settings.mistral_base_url


class NvidiaClient(OpenAICompatibleClient):
    """NVIDIA NIM -- open-weight vision models on NVIDIA's hosted endpoint.

    Included because it serves Llama 3.2 Vision, a smaller and older generation
    than the Llama 4 that SambaNova runs. Comparing the two on the same bills
    shows how much of the recent gain in handwriting OCR came from model scale
    versus from the training data, which is the sort of thing a benchmark is
    actually for.

    NVIDIA caps the size of an inline base64 data URL and rejects anything over
    it rather than degrading, so this client asks for a compressed image. See
    ``NVIDIA_MAX_IMAGE_B64_BYTES``.
    """

    provider = ModelProvider.NVIDIA
    token_param = "max_tokens"
    supports_image_detail = False
    # NIM's OpenAI shim does not implement server-side JSON mode; the prompt
    # plus parse_llm_json handles it.
    supports_json_mode = False

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.base_url = self._settings.nvidia_base_url
        self.max_image_b64_bytes = self._settings.nvidia_max_image_b64_bytes


class MoonshotClient(OpenAICompatibleClient):
    """Moonshot AI's Kimi models via their OpenAI-compatible endpoint.

    Kimi is trained with a heavy Chinese- and English-language document mix and
    is unusually strong at dense, cluttered scans -- a different training
    distribution from the Western-web-heavy models here, which is exactly the
    kind of variation worth having in a benchmark.

    ``kimi-k2.6`` is the default rather than the flagship ``kimi-k3``: k3 has
    always-on reasoning and costs $3/$15 per Mtok, while k2.6 is vision-capable,
    has a non-thinking mode and is several times cheaper. The ``moonshot-v1-*``
    vision models are deliberately avoided -- they are being sunset and are
    already unavailable to newly registered accounts.
    """

    provider = ModelProvider.MOONSHOT
    token_param = "max_tokens"
    supports_json_mode = True
    supports_image_detail = False

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.base_url = self._settings.moonshot_base_url


class OpenRouterClient(OpenAICompatibleClient):
    """OpenRouter -- a single key that fronts most other labs' models.

    Useful here for two reasons: it removes the need for a separate account per
    provider, and it is often the *only* way to reach a model whose own platform
    has no free tier. The trade is a small margin on token price and one more
    hop of latency, so a model benchmarked through OpenRouter is not strictly
    comparable on cost or speed with the same model called directly.

    Model IDs are namespaced by the originating lab -- ``moonshotai/kimi-k2.6``,
    not ``kimi-k2.6``. Sending an OpenRouter-style ID to a lab's own API (or the
    reverse) is the most common way this goes wrong.
    """

    provider = ModelProvider.OPENROUTER
    token_param = "max_tokens"
    supports_json_mode = True
    supports_image_detail = False

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.base_url = self._settings.openrouter_base_url

    def default_headers(self) -> dict[str, str]:
        """Attribution headers OpenRouter uses for its public rankings.

        Optional, and harmless if wrong -- but sending them is the polite way to
        use a service that is free-tier friendly.
        """
        return {
            "HTTP-Referer": self._settings.openrouter_site_url,
            "X-Title": self._settings.openrouter_app_name,
        }


# --------------------------------------------------------------------------
# Factory
# --------------------------------------------------------------------------

_CLIENT_REGISTRY: Final[dict[ModelProvider, type[UnifiedLLMClient]]] = {
    ModelProvider.GEMINI: GeminiClient,
    ModelProvider.CLAUDE: ClaudeClient,
    ModelProvider.OPENAI: OpenAIClient,
    ModelProvider.GROQ: GroqClient,
    ModelProvider.SAMBANOVA: SambaNovaClient,
    ModelProvider.MISTRAL: MistralClient,
    ModelProvider.NVIDIA: NvidiaClient,
    ModelProvider.MOONSHOT: MoonshotClient,
    ModelProvider.OPENROUTER: OpenRouterClient,
}

#: provider -> (settings attribute holding the key, attribute holding the model,
#: environment variable name shown in error messages)
PROVIDER_SETTINGS: Final[dict[ModelProvider, tuple[str, str, str]]] = {
    ModelProvider.GEMINI: ("google_api_key", "gemini_model", "GOOGLE_API_KEY"),
    ModelProvider.CLAUDE: ("anthropic_api_key", "claude_model", "ANTHROPIC_API_KEY"),
    ModelProvider.OPENAI: ("openai_api_key", "openai_model", "OPENAI_API_KEY"),
    ModelProvider.GROQ: ("groq_api_key", "groq_model", "GROQ_API_KEY"),
    ModelProvider.SAMBANOVA: ("sambanova_api_key", "sambanova_model", "SAMBANOVA_API_KEY"),
    ModelProvider.MISTRAL: ("mistral_api_key", "mistral_model", "MISTRAL_API_KEY"),
    ModelProvider.NVIDIA: ("nvidia_api_key", "nvidia_model", "NVIDIA_API_KEY"),
    ModelProvider.MOONSHOT: ("moonshot_api_key", "moonshot_model", "MOONSHOT_API_KEY"),
    ModelProvider.OPENROUTER: ("openrouter_api_key", "openrouter_model", "OPENROUTER_API_KEY"),
}


def build_client(provider: ModelProvider | str, settings: Settings) -> UnifiedLLMClient:
    """Construct the client for a provider slug.

    Raises:
        LLMConfigurationError: Unknown slug, or no API key configured.
    """
    try:
        key = ModelProvider(provider)
    except ValueError as exc:
        supported = ", ".join(p.value for p in ModelProvider)
        raise LLMConfigurationError(
            f"Unknown provider '{provider}'. Supported: {supported}."
        ) from exc

    allowed = settings.enabled_provider_list
    if allowed and key.value not in allowed:
        raise LLMConfigurationError(
            f"Provider '{key.value}' is not in ENABLED_PROVIDERS "
            f"({settings.enabled_providers}). Add it there to use it."
        )

    key_attr, model_attr, env_var = PROVIDER_SETTINGS[key]
    model_name = getattr(settings, model_attr)

    extra: dict[str, Any] = {}
    if key is ModelProvider.GEMINI:
        # Gemini is the one provider people run on a free tier, so it is the
        # one that actually runs out mid-benchmark. GOOGLE_API_KEY_2 is picked
        # up here as a standby; the first key present is the primary, so
        # setting only the second one still works.
        gemini_keys = settings.google_api_key_list
        api_key = gemini_keys[0] if gemini_keys else None
        extra["fallback_api_keys"] = gemini_keys[1:]
        env_var = f"{env_var} (or GOOGLE_API_KEY_2)"
    else:
        api_key = getattr(settings, key_attr)

    if not api_key:
        raise LLMConfigurationError(
            f"Provider '{key.value}' requested but {env_var} is not set in backend/.env."
        )

    return _CLIENT_REGISTRY[key](
        api_key=api_key, model_name=model_name, settings=settings, **extra
    )


def configured_models(settings: Settings) -> dict[str, str]:
    """Map each *enabled* provider slug to the model ID it points at."""
    allowed = settings.enabled_provider_list
    return {
        provider.value: getattr(settings, model_attr)
        for provider, (_, model_attr, _) in PROVIDER_SETTINGS.items()
        if not allowed or provider.value in allowed
    }


def available_providers(settings: Settings) -> list[ModelProvider]:
    """Providers that currently have a key configured."""
    return [ModelProvider(slug) for slug in settings.configured_providers]
