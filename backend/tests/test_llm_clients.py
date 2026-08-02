"""Tests for the OpenAI-compatible client family.

Groq, SambaNova, Mistral and NVIDIA all speak the same ``/chat/completions`` contract,
so one parameterised suite covers all three. A local HTTP server stands in for
the provider, which means these tests assert the *exact bytes* we put on the
wire -- endpoint path, auth header, token-limit parameter name, image encoding --
without needing an API key or a network connection.

That matters because a wrong parameter name fails only at runtime, against a
live provider, after you have already paid for the image upload.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from app.config import Settings
from app.services.llm_clients import (
    LLMConfigurationError,
    LLMResponseError,
    build_client,
    parse_llm_json,
)

# Payload the fake provider "extracts" from the image.
FAKE_FIELDS: dict[str, Any] = {
    "vendor_name": "Sharma General Store",
    "bill_number": None,
    "date": "2024-03-15",
    "amount": 245.5,
    "currency": "INR",
    "tax_gst_details": None,
}

_state: dict[str, Any] = {
    "requests": [],
    "reject_first": False,
    "hits": 0,
    "reject_message": "Unsupported parameter: 'max_completion_tokens'.",
    "reject_status": 400,
    "content_override": None,
}


class _FakeProviderHandler(BaseHTTPRequestHandler):
    """Minimal stand-in for an OpenAI-compatible chat-completions endpoint."""

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        _state["requests"].append(
            {"path": self.path, "auth": self.headers.get("Authorization"), "payload": payload}
        )
        _state["hits"] += 1

        if _state["reject_first"] and _state["hits"] == 1:
            body = json.dumps({"error": {"message": _state["reject_message"]}}).encode()
            self.send_response(_state["reject_status"])
        else:
            # Deliberately wrapped in a markdown fence with a trailing comma:
            # real models do this constantly and the client must repair it.
            content = _state["content_override"] or (
                "```json\n" + json.dumps(FAKE_FIELDS) + ",\n```"
            )
            body = json.dumps(
                {
                    "choices": [{"message": {"role": "assistant", "content": content}}],
                    "usage": {"prompt_tokens": 1483, "completion_tokens": 62},
                }
            ).encode()
            self.send_response(200)

        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        """Silence per-request logging."""


@pytest.fixture(scope="module")
def fake_provider() -> Iterator[str]:
    """Run the fake provider on an ephemeral port; yield its base URL."""
    server = HTTPServer(("127.0.0.1", 0), _FakeProviderHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    # A proxy in the environment would swallow loopback traffic.
    os.environ["NO_PROXY"] = "127.0.0.1,localhost"
    for var in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        os.environ.pop(var, None)
    try:
        yield f"http://{host}:{port}/v1"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture()
def settings(fake_provider: str) -> Settings:
    """Settings pointing every OpenAI-compatible provider at the fake server."""
    return Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        # Blank allowlist: this suite exercises the client layer, so every
        # provider must be reachable regardless of the production allowlist.
        enabled_providers="",
        groq_api_key="test-groq-key",
        sambanova_api_key="test-samba-key",
        mistral_api_key="test-mistral-key",
        nvidia_api_key="test-nvidia-key",
        moonshot_api_key="test-moonshot-key",
        openrouter_api_key="test-openrouter-key",
        groq_base_url=fake_provider,
        sambanova_base_url=fake_provider,
        mistral_base_url=fake_provider,
        nvidia_base_url=fake_provider,
        moonshot_base_url=fake_provider,
        openrouter_base_url=fake_provider,
    )


@pytest.fixture()
def bill_image(tmp_path: Path) -> str:
    """A syntactically valid bill-sized PNG on disk."""
    path = tmp_path / "bill.png"
    Image.new("RGB", (1000, 1400), (250, 248, 240)).save(path)
    return str(path)


@pytest.fixture(autouse=True)
def _reset_state() -> Iterator[None]:
    _state["requests"] = []
    _state["reject_first"] = False
    _state["hits"] = 0
    _state["reject_message"] = "Unsupported parameter: 'max_completion_tokens'."
    _state["reject_status"] = 400
    _state["content_override"] = None
    yield


# ---------------------------------------------------------------------------
# Wire format
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "token_param", "json_mode"),
    [
        ("groq", "max_completion_tokens", True),
        ("sambanova", "max_tokens", True),
        ("mistral", "max_tokens", True),
        ("moonshot", "max_tokens", True),
        ("openrouter", "max_tokens", True),
        # NVIDIA NIM's OpenAI shim has no server-side JSON mode; the prompt and
        # parse_llm_json carry it instead.
        ("nvidia", "max_tokens", False),
    ],
)
async def test_request_shape(
    provider: str, token_param: str, json_mode: bool, settings: Settings, bill_image: str
) -> None:
    """Each provider gets the exact payload its API expects."""
    client = build_client(provider, settings)
    outcome = await client.extract_bill_data(bill_image)

    assert outcome.succeeded, outcome.error_message
    request = _state["requests"][-1]

    assert request["path"].endswith("/chat/completions")
    assert request["auth"].startswith("Bearer ")
    assert token_param in request["payload"]
    if json_mode:
        assert request["payload"]["response_format"] == {"type": "json_object"}
    else:
        assert "response_format" not in request["payload"]

    content = request["payload"]["messages"][0]["content"]
    image_block = next(b for b in content if b["type"] == "image_url")
    assert image_block["image_url"]["url"].startswith("data:image/")
    assert any(b["type"] == "text" and "invoice parser" in b["text"] for b in content)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["groq", "sambanova", "mistral", "nvidia", "moonshot"])
async def test_response_parsing_and_telemetry(
    provider: str, settings: Settings, bill_image: str
) -> None:
    """Fenced JSON with a trailing comma is repaired; usage data is recorded."""
    client = build_client(provider, settings)
    outcome = await client.extract_bill_data(bill_image)

    assert outcome.fields["vendor_name"] == "Sharma General Store"
    assert outcome.fields["amount"] == 245.5
    assert outcome.token_source == "provider"
    assert outcome.input_tokens == 1483
    assert outcome.output_tokens == 62
    assert outcome.cost_usd > 0
    assert outcome.latency_ms >= 0


# ---------------------------------------------------------------------------
# Resilience
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_degrades_when_provider_rejects_a_parameter(
    settings: Settings, bill_image: str
) -> None:
    """A rejected token parameter triggers exactly one retry with the other name."""
    _state["reject_first"] = True

    client = build_client("groq", settings)
    outcome = await client.extract_bill_data(bill_image)

    assert outcome.succeeded, outcome.error_message
    assert len(_state["requests"]) == 2, "should retry exactly once"

    retry_payload = _state["requests"][1]["payload"]
    assert "max_tokens" in retry_payload
    assert "max_completion_tokens" not in retry_payload


@pytest.mark.asyncio
async def test_missing_image_is_reported_not_raised(settings: Settings) -> None:
    """A bad path comes back as a failed outcome, never as an exception."""
    client = build_client("groq", settings)
    outcome = await client.extract_bill_data("/nonexistent/bill.png")

    assert outcome.succeeded is False
    assert outcome.error_message is not None
    assert "not found" in outcome.error_message.lower()


class TestReasoningModels:
    """Groq's vision model thinks out loud unless told not to."""

    @pytest.mark.asyncio
    async def test_groq_disables_reasoning(self, settings: Settings, bill_image: str) -> None:
        """Chain of thought breaks JSON output and burns the token budget."""
        client = build_client("groq", settings)
        await client.extract_bill_data(bill_image)
        payload = _state["requests"][-1]["payload"]

        assert payload["reasoning_effort"] == "none"
        # `raw` is rejected alongside JSON mode, so it must be pinned.
        assert payload["reasoning_format"] == "hidden"

    @pytest.mark.asyncio
    async def test_extras_travel_via_extra_body_not_kwargs(
        self, settings: Settings, bill_image: str
    ) -> None:
        """The SDK raises TypeError on unknown kwargs; extra_body is merged
        into the JSON body instead. Getting this wrong fails silently -- the
        params simply never reach the provider."""
        client = build_client("groq", settings)
        payload = client._build_payload("Zm9v", "image/jpeg")

        assert "reasoning_format" not in payload
        assert payload["extra_body"]["reasoning_format"] == "hidden"

    @pytest.mark.asyncio
    async def test_reasoning_params_can_be_switched_off(
        self, fake_provider: str, bill_image: str
    ) -> None:
        """A non-Qwen Groq model would reject them, so blank sends nothing."""
        settings = Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            enabled_providers="",
            groq_api_key="k",
            mistral_api_key="k",
            groq_base_url=fake_provider,
            groq_reasoning_effort="",
        )
        client = build_client("groq", settings)
        await client.extract_bill_data(bill_image)
        payload = _state["requests"][-1]["payload"]

        assert "reasoning_effort" not in payload
        assert "reasoning_format" not in payload

    @pytest.mark.asyncio
    async def test_rejected_reasoning_param_is_dropped_and_retried(
        self, settings: Settings, bill_image: str
    ) -> None:
        _state["reject_first"] = True
        _state["reject_message"] = "'reasoning_effort' is not supported by this model"

        client = build_client("groq", settings)
        outcome = await client.extract_bill_data(bill_image)

        assert outcome.succeeded, outcome.error_message
        assert "reasoning_effort" not in _state["requests"][1]["payload"]

    @pytest.mark.asyncio
    async def test_json_validate_failed_retries_without_json_mode(
        self, settings: Settings, bill_image: str
    ) -> None:
        """Groq's json_validate_failed means the model emitted nothing usable.

        Dropping the decoder constraint gets raw text back, which the repair
        pass can still salvage -- better than failing the whole extraction.
        """
        _state["reject_first"] = True
        _state["reject_message"] = (
            "Failed to validate JSON. Please adjust your prompt. "
            "See 'failed_generation' for more details. code: json_validate_failed"
        )

        client = build_client("groq", settings)
        outcome = await client.extract_bill_data(bill_image)

        assert outcome.succeeded, outcome.error_message
        assert "response_format" not in _state["requests"][1]["payload"]
        assert outcome.fields["vendor_name"] == "Sharma General Store"

    @pytest.mark.asyncio
    async def test_think_block_is_stripped_from_the_answer(
        self, settings: Settings, bill_image: str
    ) -> None:
        """Belt and braces: even if reasoning leaks through, we recover."""
        _state["content_override"] = (
            "<think>\nThe user wants me to parse a handwritten bill.\n"
            "The date looks like 2-2-1968.\n</think>\n" + json.dumps(FAKE_FIELDS)
        )
        client = build_client("groq", settings)
        outcome = await client.extract_bill_data(bill_image)

        assert outcome.succeeded, outcome.error_message
        assert outcome.fields["vendor_name"] == "Sharma General Store"

    @pytest.mark.asyncio
    async def test_truncated_reasoning_gives_an_actionable_error(
        self, settings: Settings, bill_image: str
    ) -> None:
        """An unterminated <think> means the budget ran out mid-thought."""
        _state["content_override"] = "<think>Analysing the image. The header reads"
        client = build_client("groq", settings)
        outcome = await client.extract_bill_data(bill_image)

        assert outcome.succeeded is False
        assert "reasoning" in (outcome.error_message or "").lower()


class TestImageBudget:
    """NVIDIA rejects oversized inline images, so we compress to fit."""

    @pytest.mark.asyncio
    async def test_nvidia_payload_respects_the_cap(
        self, fake_provider: str, tmp_path: Path
    ) -> None:
        """A detailed photo is recompressed below the provider's ceiling."""
        # Noise compresses badly, standing in for a real high-detail phone photo.
        import random

        random.seed(7)
        noisy = tmp_path / "noisy.jpg"
        image = Image.new("RGB", (3000, 2200))
        image.putdata(
            [
                (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
                for _ in range(3000 * 2200)
            ]
        )
        image.save(noisy, quality=95)

        cap = 180_000
        settings = Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            enabled_providers="",
            nvidia_api_key="k",
            groq_api_key="k",
            nvidia_base_url=fake_provider,
            nvidia_max_image_b64_bytes=cap,
        )
        client = build_client("nvidia", settings)
        outcome = await client.extract_bill_data(str(noisy))

        assert outcome.succeeded, outcome.error_message
        url = next(
            b
            for b in _state["requests"][-1]["payload"]["messages"][0]["content"]
            if b["type"] == "image_url"
        )["image_url"]["url"]
        payload_b64 = url.split(",", 1)[1]
        assert len(payload_b64) <= cap, f"payload was {len(payload_b64)} bytes, cap {cap}"

    @pytest.mark.asyncio
    async def test_providers_without_a_cap_send_full_quality(
        self, settings: Settings, bill_image: str
    ) -> None:
        client = build_client("groq", settings)
        assert client.max_image_b64_bytes is None
        outcome = await client.extract_bill_data(bill_image)
        assert outcome.succeeded


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------


class TestCostEstimation:
    def test_known_model_uses_published_rate(self, settings: Settings) -> None:
        client = build_client("groq", settings)
        # qwen/qwen3.6-27b is $0.60 in / $3.00 out per 1M tokens.
        assert client.estimate_cost(1_000_000, 0) == pytest.approx(0.60)
        assert client.estimate_cost(0, 1_000_000) == pytest.approx(3.00)

    def test_env_override_wins(self, fake_provider: str) -> None:
        """Published prices drift; the override must not require a code change."""
        settings = Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            enabled_providers="",
            groq_api_key="k",
            mistral_api_key="k",
            groq_base_url=fake_provider,
            model_price_overrides={"qwen/qwen3.6-27b": [99.0, 1.0]},
        )
        client = build_client("groq", settings)
        assert client.estimate_cost(1_000_000, 0) == pytest.approx(99.0)

    def test_unknown_model_falls_back_rather_than_raising(self, fake_provider: str) -> None:
        settings = Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            enabled_providers="",
            groq_api_key="k",
            mistral_api_key="k",
            groq_model="some/model-released-tomorrow",
            groq_base_url=fake_provider,
        )
        client = build_client("groq", settings)
        assert client.estimate_cost(1_000_000, 1_000_000) > 0


class TestAuthDiagnostics:
    """A 401 usually means a valid key aimed at the wrong service."""

    def _client(self, provider: str, api_key: str, fake_provider: str):
        settings = Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            enabled_providers="",
            google_api_key="k",
            groq_api_key=api_key if provider == "groq" else "k",
            moonshot_api_key=api_key if provider == "moonshot" else "k",
            openrouter_api_key=api_key if provider == "openrouter" else "k",
            nvidia_api_key=api_key if provider == "nvidia" else "k",
            moonshot_base_url=fake_provider,
            openrouter_base_url=fake_provider,
            groq_base_url=fake_provider,
            nvidia_base_url=fake_provider,
        )
        return build_client(provider, settings)

    def test_openrouter_key_sent_to_moonshot_is_named(self, fake_provider: str) -> None:
        """The exact mistake that costs an afternoon: right key, wrong endpoint."""
        client = self._client("moonshot", "sk-or-v1-abc123", fake_provider)
        message = client.diagnose_auth_failure()

        assert "sk-or-" in message
        assert "openrouter" in message
        assert "OPENROUTER_API_KEY" in message

    @pytest.mark.parametrize(
        ("provider", "key", "expected_owner"),
        [
            ("moonshot", "gsk_abc123", "groq"),
            ("openrouter", "nvapi-abc123", "nvidia"),
            ("groq", "sk-ant-abc123", "claude"),
        ],
    )
    def test_other_mismatches_are_named(
        self, provider: str, key: str, expected_owner: str, fake_provider: str
    ) -> None:
        client = self._client(provider, key, fake_provider)
        assert expected_owner in client.diagnose_auth_failure()

    def test_plausible_key_gets_generic_advice(self, fake_provider: str) -> None:
        """No prefix match means we must not guess at a culprit."""
        client = self._client("moonshot", "sk-plausible-moonshot-key", fake_provider)
        message = client.diagnose_auth_failure()

        assert "MOONSHOT_API_KEY" in message
        assert "openrouter" not in message

    @pytest.mark.asyncio
    async def test_401_surfaces_the_diagnosis_not_a_bare_error(
        self, fake_provider: str, bill_image: str
    ) -> None:
        _state["reject_first"] = True
        _state["reject_status"] = 401
        _state["reject_message"] = "Invalid Authentication"

        client = self._client("moonshot", "sk-or-v1-wrongservice", fake_provider)
        outcome = await client.extract_bill_data(bill_image)

        assert outcome.succeeded is False
        assert "openrouter" in (outcome.error_message or "")
        # Must not be retried -- a 401 is not something a reduced payload fixes.
        assert len(_state["requests"]) == 1


class TestOpenRouter:
    def test_sends_attribution_headers(self, settings: Settings) -> None:
        client = build_client("openrouter", settings)
        headers = client.default_headers()

        assert "HTTP-Referer" in headers
        assert headers["X-Title"] == "Lextract"

    def test_namespaced_model_id_is_priced(self, settings: Settings) -> None:
        """OpenRouter slugs differ from native ones and need their own entries."""
        client = build_client("openrouter", settings)

        assert client.get_model_name() == "moonshotai/kimi-k2.6"
        assert client.estimate_cost(1_000_000, 0) == pytest.approx(0.60)


class TestProviderAllowlist:
    """ENABLED_PROVIDERS controls what the app offers, without deleting code."""

    def _settings(self, allowlist: str) -> Settings:
        return Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            enabled_providers=allowlist,
            google_api_key="k",
            groq_api_key="k",
            sambanova_api_key="k",
            mistral_api_key="k",
            nvidia_api_key="k",
            moonshot_api_key="k",
        )

    def test_only_allowlisted_providers_are_offered(self) -> None:
        """Keys exist for all six; only the four allowed ones are exposed."""
        settings = self._settings("gemini,groq,moonshot,nvidia")
        assert settings.configured_providers == ["gemini", "groq", "nvidia", "moonshot"]

    def test_disabled_provider_cannot_be_built(self) -> None:
        """The API must refuse it too, not just hide it in the UI."""
        settings = self._settings("gemini,groq,moonshot,nvidia")
        with pytest.raises(LLMConfigurationError, match="ENABLED_PROVIDERS"):
            build_client("sambanova", settings)

    def test_blank_allowlist_permits_everything(self) -> None:
        settings = self._settings("")
        assert "sambanova" in settings.configured_providers
        assert "mistral" in settings.configured_providers

    def test_configured_models_respects_the_allowlist(self) -> None:
        """/api/health must not advertise a model the API will reject."""
        from app.services.llm_clients import configured_models

        models = configured_models(self._settings("gemini,moonshot"))
        assert set(models) == {"gemini", "moonshot"}

    def test_whitespace_and_case_are_tolerated(self) -> None:
        settings = self._settings(" Gemini , GROQ ")
        assert settings.configured_providers == ["gemini", "groq"]


# ---------------------------------------------------------------------------
# JSON repair
# ---------------------------------------------------------------------------


class TestJsonRepair:
    def test_plain_json(self) -> None:
        assert parse_llm_json('{"a": 1}') == {"a": 1}

    def test_markdown_fence(self) -> None:
        assert parse_llm_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_preamble_prose(self) -> None:
        assert parse_llm_json('Here is the data:\n{"a": 1}\nHope that helps!') == {"a": 1}

    def test_trailing_comma(self) -> None:
        assert parse_llm_json('{"a": 1,}') == {"a": 1}

    def test_single_element_array_wrapper(self) -> None:
        assert parse_llm_json('[{"a": 1}]') == {"a": 1}

    def test_empty_response_raises(self) -> None:
        with pytest.raises(LLMResponseError, match="empty"):
            parse_llm_json("   ")

    def test_unparseable_raises_with_preview(self) -> None:
        with pytest.raises(LLMResponseError, match="not valid JSON"):
            parse_llm_json("I cannot read this bill, sorry.")

    def test_strips_closed_think_block(self) -> None:
        assert parse_llm_json('<think>Let me look...</think>{"a": 1}') == {"a": 1}

    def test_strips_think_block_inside_a_fence(self) -> None:
        assert parse_llm_json('```json\n<think>hmm</think>\n{"a": 1,}\n```') == {"a": 1}

    def test_unterminated_think_block_names_the_real_cause(self) -> None:
        with pytest.raises(LLMResponseError, match="budget reasoning"):
            parse_llm_json("<think>Analysing the bill header, which reads")


class TestMalformedJsonRecovery:
    """Real model output, from the failures this parser was built against.

    Every case here is a *correct read* wrapped in a broken container. Failing
    them would measure format compliance instead of extraction accuracy.
    """

    @pytest.mark.parametrize(
        ("label", "raw"),
        [
            (
                "extra closing brace",
                '{"vendor_name": "ABC Medicals", "amount": 224}\n}',
            ),
            ("several extra braces", '{"vendor_name": "ABC Medicals"}}}'),
            ("json fence", '```json\n{"vendor_name": "ABC Medicals"}\n```'),
            ("unclosed fence", '```json\n{"vendor_name": "ABC Medicals"}'),
            ("leading prose", 'Sure! Here is the data:\n{"vendor_name": "ABC Medicals"}'),
            ("trailing prose", '{"vendor_name": "ABC Medicals"}\n\nHope that helps!'),
            (
                "prose both sides, fenced, extra brace",
                'Here:\n```json\n{"vendor_name": "ABC Medicals"}\n```\n}\nDone!',
            ),
            ("trailing comma", '{"vendor_name": "ABC Medicals", "amount": 1,}'),
            ("python literals", '{"vendor_name": "ABC Medicals", "bill_number": None}'),
            ("smart quotes", '{\u201cvendor_name\u201d: \u201cABC Medicals\u201d}'),
            ("single-element array", '[{"vendor_name": "ABC Medicals"}]'),
            ("reasoning block", '<think>hmm</think>{"vendor_name": "ABC Medicals"}'),
        ],
    )
    def test_recovers_vendor_name(self, label: str, raw: str) -> None:
        assert parse_llm_json(raw)["vendor_name"] == "ABC Medicals", label

    def test_first_complete_object_wins(self) -> None:
        """Scanning for balance beats slicing find/rfind, which would span both."""
        assert parse_llm_json('{"vendor_name": "First"}\n{"vendor_name": "Second"}') == {
            "vendor_name": "First"
        }

    def test_braces_inside_strings_do_not_confuse_the_scanner(self) -> None:
        parsed = parse_llm_json('{"vendor_name": "A{B}C", "amount": 1}')
        assert parsed["vendor_name"] == "A{B}C"

    def test_escaped_quote_inside_string(self) -> None:
        parsed = parse_llm_json('{"vendor_name": "He said \\"hi\\"", "amount": 2}')
        assert parsed["amount"] == 2

    def test_still_raises_on_a_genuine_refusal(self) -> None:
        """Recovery must not mean inventing data from nothing."""
        with pytest.raises(LLMResponseError):
            parse_llm_json("I cannot read this bill, sorry.")


class TestJsonPipelineParts:
    """The pipeline stages are individually testable on purpose."""

    def test_strip_code_fences(self) -> None:
        from app.services.llm_clients import strip_code_fences

        assert strip_code_fences('```json\n{"a": 1}\n```') == '{"a": 1}'

    def test_iter_json_objects_finds_each_object(self) -> None:
        from app.services.llm_clients import iter_json_objects

        found = list(iter_json_objects('noise {"a": 1} more {"b": 2} tail'))
        assert found == ['{"a": 1}', '{"b": 2}']

    def test_iter_json_objects_ignores_unbalanced_tail(self) -> None:
        from app.services.llm_clients import iter_json_objects

        assert list(iter_json_objects('{"a": 1}\n}')) == ['{"a": 1}']

    def test_repair_json_is_syntax_only(self) -> None:
        """Repairs must never alter a value the model actually read."""
        from app.services.llm_clients import repair_json

        assert repair_json('{"a": 1,}') == '{"a": 1}'
        assert "245.50" in repair_json('{"amount": 245.50,}')


class TestTruncatedJson:
    """Output cut off by the token limit still carries usable fields."""

    def test_missing_closing_brace_is_repaired(self) -> None:
        truncated = (
            '{\n "vendor_name": "Kmart",\n "bill_number": "E 1123657",\n'
            ' "date": "1981-12-20",\n "amount": 211.60,\n "currency": "USD",\n'
            ' "tax_gst_details": null'
        )
        parsed = parse_llm_json(truncated)

        assert parsed["vendor_name"] == "Kmart"
        assert parsed["amount"] == 211.60
        assert parsed["tax_gst_details"] is None

    def test_truncated_mid_string_keeps_completed_pairs(self) -> None:
        """Rewind to the last complete pair rather than invent a closing quote."""
        parsed = parse_llm_json('{"vendor_name": "Sharma Store", "bill_number": "INV-')

        assert parsed["vendor_name"] == "Sharma Store"
        assert "bill_number" not in parsed

    def test_truncated_nested_structure(self) -> None:
        parsed = parse_llm_json('{"a": 1, "b": {"c": 2')
        assert parsed["a"] == 1

    def test_complete_json_is_untouched(self) -> None:
        assert parse_llm_json('{"a": 1}') == {"a": 1}


class TestProseFallback:
    """A correct read in the wrong container is still a correct read."""

    PROSE = """The invoice shows the following details:

* **Vendor Name**: Kmart
* **Bill Number**: 3288
* **Date**: 12/20/81
* **Amount**: 131.88
* **Currency**: INR
* **Tax/GST Details**: Not provided
"""

    def test_markdown_bullets_are_recovered(self) -> None:
        parsed = parse_llm_json(self.PROSE)

        assert parsed["vendor_name"] == "Kmart"
        assert parsed["bill_number"] == "3288"
        assert parsed["amount"] == "131.88"
        assert parsed["currency"] == "INR"

    def test_not_provided_becomes_null(self) -> None:
        """Otherwise the evaluator would score the literal string as a wrong
        answer instead of an honest miss."""
        assert parse_llm_json(self.PROSE)["tax_gst_details"] is None

    def test_numbered_list_is_recovered(self) -> None:
        """NVIDIA's Llama Vision answers in a numbered list under a preamble."""
        parsed = parse_llm_json(
            "To extract the required fields, we can follow the rules provided:\n\n"
            "1. Vendor Name: ABC Medicals\n"
            "2. Bill Number: 5487\n"
            "3. Amount: 222.9\n"
        )
        assert parsed["vendor_name"] == "ABC Medicals"
        assert parsed["bill_number"] == "5487"

    def test_a_header_line_does_not_swallow_the_next_field(self) -> None:
        """'The receipt shows:' has no value on its line; it must not consume
        the field below it."""
        parsed = parse_llm_json("The receipt shows:\n\n* **Vendor Name**: ABC Medicals")
        assert parsed["vendor_name"] == "ABC Medicals"

    def test_plain_colon_lines_work(self) -> None:
        parsed = parse_llm_json("Vendor: Verma Medicals\nTotal: 890.00\nDate: 2024-03-18")

        assert parsed["vendor_name"] == "Verma Medicals"
        assert parsed["amount"] == "890.00"

    def test_json_still_wins_over_prose(self) -> None:
        """The prose path is a last resort, not a competing parser."""
        mixed = 'Here you go:\n{"vendor_name": "From JSON"}\nVendor Name: From prose'
        assert parse_llm_json(mixed)["vendor_name"] == "From JSON"

    def test_unrecognisable_prose_still_raises(self) -> None:
        with pytest.raises(LLMResponseError):
            parse_llm_json("I am unable to read this bill, sorry.")


class TestGeminiThinking:
    """Gemini charges thinking tokens against max_output_tokens."""

    def test_config_ladder_tries_both_parameter_names(self) -> None:
        """3.x takes thinking_level, 2.x takes thinking_budget; branching on a
        model-name regex would break at the next rename."""
        from app.services.llm_clients import GeminiClient

        settings = Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            enabled_providers="",
            google_api_key="k",
            groq_api_key="k",
        )
        client = GeminiClient(
            api_key="k", model_name="gemini-3.5-flash", settings=settings
        )

        class FakeThinkingConfig:
            def __init__(self, **kwargs: object) -> None:
                self.kwargs = kwargs

        class FakeTypes:
            ThinkingConfig = FakeThinkingConfig

        attempts = client._thinking_configs(FakeTypes)

        assert len(attempts) == 3
        assert attempts[0]["thinking_config"].kwargs == {"thinking_level": "low"}
        assert attempts[1]["thinking_config"].kwargs == {"thinking_budget": 0}
        assert attempts[2] == {}, "must end with an empty config as the fallback"

    def test_blank_setting_sends_nothing(self) -> None:
        from app.services.llm_clients import GeminiClient

        settings = Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            enabled_providers="",
            google_api_key="k",
            groq_api_key="k",
            gemini_thinking_level="",
        )
        client = GeminiClient(api_key="k", model_name="gemini-3.5-flash", settings=settings)

        assert client._thinking_configs(object()) == [{}]
