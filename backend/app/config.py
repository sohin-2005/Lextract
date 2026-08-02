"""Lextract configuration.

Every secret is read from the environment (or a local ``.env`` file that is
git-ignored).  No literal API key ever appears in this repository.

Design note
-----------
``Settings`` is cached with :func:`functools.lru_cache` so the ``.env`` file is
parsed exactly once per process.  Import ``get_settings()`` rather than a
module-level singleton so that tests can clear the cache and inject overrides.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Final

from pydantic import Field, computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

BACKEND_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
PROJECT_ROOT: Final[Path] = BACKEND_ROOT.parent

# Tests set TAXOR_DISABLE_DOTENV=1 (see tests/conftest.py) so the suite never
# reads the developer's real .env. Without this a test could pick up live
# credentials and make a billable API call against a real account.
_ENV_FILE: Final[str | None] = (
    None if os.getenv("TAXOR_DISABLE_DOTENV") else str(BACKEND_ROOT / ".env")
)


class Settings(BaseSettings):
    """Typed, validated application settings."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        protected_namespaces=(),
    )

    # ------------------------------------------------------------------ DB
    database_url: str = Field(
        default="postgresql+asyncpg://lextract:lextract@localhost:5432/lextract",
        description="SQLAlchemy DSN. The asyncpg driver is added automatically.",
    )

    # ---------------------------------------------------------- LLM secrets
    google_api_key: str | None = Field(default=None, description="Google AI Studio key.")
    anthropic_api_key: str | None = Field(default=None, description="console.anthropic.com key.")
    openai_api_key: str | None = Field(default=None, description="platform.openai.com key.")

    # OpenAI-compatible providers. Each speaks the same /chat/completions wire
    # format, so they need only a key and a base URL -- no extra client code.
    groq_api_key: str | None = Field(default=None, description="console.groq.com key.")
    sambanova_api_key: str | None = Field(default=None, description="cloud.sambanova.ai key.")
    mistral_api_key: str | None = Field(default=None, description="console.mistral.ai key.")
    nvidia_api_key: str | None = Field(default=None, description="build.nvidia.com NIM key.")
    moonshot_api_key: str | None = Field(default=None, description="platform.kimi.ai key.")
    openrouter_api_key: str | None = Field(default=None, description="openrouter.ai key.")

    # ------------------------------------------------------------ LLM model
    # Model IDs live in config, never in code, so a deprecation is a one-line
    # .env change rather than a code change.
    # 2.5-flash is closed to new API accounts, so a fresh key gets a 404 on it.
    gemini_model: str = "gemini-3.5-flash"
    claude_model: str = "claude-haiku-4-5-20251001"
    openai_model: str = "gpt-5-mini"
    groq_model: str = "qwen/qwen3.6-27b"
    sambanova_model: str = "Llama-4-Maverick-17B-128E-Instruct"
    mistral_model: str = "pixtral-12b-latest"
    nvidia_model: str = "meta/llama-3.2-11b-vision-instruct"
    moonshot_model: str = "kimi-k2.6"
    # OpenRouter namespaces every model by its originating lab.
    openrouter_model: str = "moonshotai/kimi-k2.6"

    # Base URLs for the OpenAI-compatible providers. In config so a provider
    # moving its endpoint is a .env edit, not a code change.
    openai_base_url: str | None = None  # None -> the SDK default
    groq_base_url: str = "https://api.groq.com/openai/v1"
    sambanova_base_url: str = "https://api.sambanova.ai/v1"
    mistral_base_url: str = "https://api.mistral.ai/v1"
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    moonshot_base_url: str = "https://api.moonshot.ai/v1"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Optional attribution headers OpenRouter uses for its public rankings.
    openrouter_site_url: str = "http://localhost:5173"
    openrouter_app_name: str = "Lextract"

    gemini_thinking_level: str = Field(
        default="low",
        description=(
            "Gemini 2.5+ thinks by default and charges those tokens against "
            "max_output_tokens, which truncates the JSON mid-object. "
            "Transcription needs no reasoning. Blank disables the setting "
            "entirely if your model rejects it."
        ),
    )

    groq_reasoning_effort: str = Field(
        default="none",
        description=(
            "Groq's vision model (Qwen 3.6) is a reasoning model whose chain of "
            "thought both breaks JSON output and eats the token budget. 'none' "
            "disables it, which is what you want for transcription. Set to an "
            "empty string to send no reasoning parameters at all."
        ),
    )

    nvidia_max_image_b64_bytes: int = Field(
        default=180_000,
        description=(
            "Ceiling on the base64 image payload sent to NVIDIA NIM. Their "
            "hosted endpoint caps inline data URLs and rejects larger ones "
            "outright; anything above this is recompressed rather than failed. "
            "Raise it if your account permits bigger inline images."
        ),
    )

    llm_timeout_seconds: float = 90.0
    # 1024 is Groq's default and is uncomfortably tight: a dense bill plus
    # any residual reasoning can exhaust it before the JSON closes.
    llm_max_output_tokens: int = 2048

    model_price_overrides: dict[str, list[float]] = Field(
        default_factory=dict,
        description=(
            "Per-model USD rates per 1M tokens, as "
            '{"model-id": [input_rate, output_rate]}. Published prices drift; '
            "this lets you correct the cost column without touching source."
        ),
    )

    enabled_providers: str = Field(
        default="gemini,groq,moonshot,nvidia",
        description=(
            "Allowlist of provider slugs the app will offer. Clients for every "
            "provider stay in the codebase -- this only controls which ones "
            "appear in the UI, the API and the report. Blank means all of them. "
            "Keeping it an allowlist rather than deleting code means re-enabling "
            "a provider is a .env edit, not a revert."
        ),
    )

    allow_single_provider: bool = Field(
        default=False,
        description=(
            "The evaluation task is a *comparison*, so two providers is the "
            "real minimum. Set true only if you deliberately want to run with one."
        ),
    )

    # ----------------------------------------------------------------- Zoho
    zoho_client_id: str | None = None
    zoho_client_secret: str | None = None
    zoho_refresh_token: str | None = None
    zoho_organization_id: str | None = None
    zoho_redirect_uri: str = "http://localhost:5173/zoho/callback"
    zoho_accounts_domain: str = "https://accounts.zoho.in"
    zoho_books_base_url: str = "https://www.zohoapis.in/books/v3"
    zoho_default_expense_account: str = "Other Expenses"
    zoho_expense_account_id: str = Field(
        default="",
        description=(
            "Zoho chart-of-accounts id to book expenses against. Setting this "
            "skips the name->id lookup entirely, which is the only part of the "
            "flow needing ZohoBooks.accountants.READ -- so a narrow token still "
            "works. Find it in the URL when you open the account in Zoho Books "
            "-> Accountant -> Chart of Accounts."
        ),
    )
    zoho_scope: str = Field(
        default="",
        description=(
            "OAuth scopes to request. Blank uses the narrow per-module default. "
            "Set to ZohoBooks.fullaccess.all if scope debugging is not how you "
            "want to spend the afternoon. Scopes are baked into the refresh "
            "token, so changing this means regenerating it."
        ),
    )

    # ------------------------------------------------------------------ App
    upload_dir: Path = Field(default=Path("/tmp/bills"))
    max_image_size_mb: int = 10
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000"
    log_level: str = "INFO"

    # ----------------------------------------------------------- validators
    @field_validator("database_url")
    @classmethod
    def _force_async_driver(cls, value: str) -> str:
        """Upgrade a plain ``postgresql://`` DSN to the asyncpg driver.

        Users routinely paste the psycopg-style URL printed by ``createdb``
        tutorials. Silently repairing it removes the single most common
        first-run failure without hiding anything (we log the rewrite).
        """
        if value.startswith("postgresql+asyncpg://"):
            return value
        if value.startswith(("postgresql://", "postgres://")):
            scheme, _, rest = value.partition("://")
            logger.warning(
                "DATABASE_URL used the sync driver (%s://); rewriting to postgresql+asyncpg://",
                scheme,
            )
            return f"postgresql+asyncpg://{rest}"
        return value

    @field_validator(
        "groq_base_url",
        "sambanova_base_url",
        "mistral_base_url",
        "nvidia_base_url",
        "moonshot_base_url",
        "openrouter_base_url",
        "openai_base_url",
    )
    @classmethod
    def _strip_endpoint_path(cls, value: str | None) -> str | None:
        """Normalise a provider base URL to its root.

        Every provider documents its endpoint as
        ``https://host/v1/chat/completions``, so that is what people paste. But
        the OpenAI SDK appends ``/chat/completions`` itself, and the doubled
        path produces a 404 that looks like a broken model ID rather than a
        broken URL. Trim the suffix and the trailing slash instead of letting
        that happen.
        """
        if not value:
            return value
        cleaned = value.strip().rstrip("/")
        for suffix in ("/chat/completions", "/completions"):
            if cleaned.endswith(suffix):
                cleaned = cleaned[: -len(suffix)]
                logger.warning(
                    "Base URL %r included the endpoint path; using %r.", value, cleaned
                )
                break
        return cleaned

    @field_validator("upload_dir")
    @classmethod
    def _resolve_upload_dir(cls, value: Path) -> Path:
        if not value.is_absolute():
            value = PROJECT_ROOT / value

        value.mkdir(parents=True, exist_ok=True)
        return value.resolve()

    @model_validator(mode="after")
    def _require_two_providers(self) -> "Settings":
        if len(self.configured_providers) < 2 and not self.allow_single_provider:
            raise ValueError(
                "This project benchmarks LLMs against each other, so it needs at "
                f"least 2 provider keys. Found {len(self.configured_providers)}: "
                f"{self.configured_providers or 'none'}. Set any two of "
                "GOOGLE_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY, GROQ_API_KEY, "
                "SAMBANOVA_API_KEY, MISTRAL_API_KEY, NVIDIA_API_KEY or "
                "MOONSHOT_API_KEY in backend/.env (and make sure the provider is "
                "listed in ENABLED_PROVIDERS), or set "
                "ALLOW_SINGLE_PROVIDER=true to bypass this check."
            )
        return self

    # ----------------------------------------------------------- properties
    @computed_field  # type: ignore[prop-decorator]
    @property
    def configured_providers(self) -> list[str]:
        """Provider slugs that have a non-empty API key."""
        pairs = (
            ("gemini", self.google_api_key),
            ("claude", self.anthropic_api_key),
            ("openai", self.openai_api_key),
            ("groq", self.groq_api_key),
            ("sambanova", self.sambanova_api_key),
            ("mistral", self.mistral_api_key),
            ("nvidia", self.nvidia_api_key),
            ("moonshot", self.moonshot_api_key),
            ("openrouter", self.openrouter_api_key),
        )
        allowed = self.enabled_provider_list
        return [
            slug
            for slug, key in pairs
            if key and key.strip() and (not allowed or slug in allowed)
        ]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def zoho_configured(self) -> bool:
        """True when every credential needed for a Books API call is present."""
        return all(
            bool(v and v.strip())
            for v in (
                self.zoho_client_id,
                self.zoho_client_secret,
                self.zoho_refresh_token,
                self.zoho_organization_id,
            )
        )

    @property
    def enabled_provider_list(self) -> list[str]:
        """Allowlisted provider slugs; empty list means "no restriction"."""
        return [p.strip().lower() for p in self.enabled_providers.split(",") if p.strip()]

    @property
    def max_image_size_bytes(self) -> int:
        return self.max_image_size_mb * 1024 * 1024

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide :class:`Settings` singleton."""
    return Settings()
