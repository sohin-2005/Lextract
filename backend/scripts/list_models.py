#!/usr/bin/env python3
"""List the models each configured provider actually offers, right now.

Model IDs churn constantly -- providers deprecate a vision model and the only
symptom is a 404 on your next extraction. Rather than guessing at a name from a
blog post, run this and copy an ID straight into ``backend/.env``.

Usage
-----
    cd backend
    python scripts/list_models.py              # every configured provider
    python scripts/list_models.py groq         # just one
    python scripts/list_models.py --all        # do not filter to vision models

Only providers with a key in ``backend/.env`` are contacted. Uses the standard
library, so it runs before ``pip install``.
"""

from __future__ import annotations

import json
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = BACKEND_ROOT / ".env"

# provider -> (env key, default endpoint, style)
PROVIDERS: dict[str, tuple[str, str, str]] = {
    "gemini": ("GOOGLE_API_KEY", "https://generativelanguage.googleapis.com/v1beta/models", "google"),
    "claude": ("ANTHROPIC_API_KEY", "https://api.anthropic.com/v1/models", "anthropic"),
    "openai": ("OPENAI_API_KEY", "https://api.openai.com/v1/models", "openai"),
    "groq": ("GROQ_API_KEY", "https://api.groq.com/openai/v1/models", "openai"),
    "sambanova": ("SAMBANOVA_API_KEY", "https://api.sambanova.ai/v1/models", "openai"),
    "mistral": ("MISTRAL_API_KEY", "https://api.mistral.ai/v1/models", "openai"),
}

# Which .env var overrides the base URL, where one exists.
BASE_URL_VARS = {
    "groq": "GROQ_BASE_URL",
    "sambanova": "SAMBANOVA_BASE_URL",
    "mistral": "MISTRAL_BASE_URL",
    "openai": "OPENAI_BASE_URL",
}

# Substrings that suggest a model can see images. Heuristic only -- most
# providers do not flag vision capability in their /models payload, so `--all`
# exists for when the guess is wrong.
VISION_HINTS = (
    "vision", "vl", "pixtral", "llama-4", "llama4", "maverick", "scout",
    "qwen3", "qwen2.5", "gemini", "claude", "gpt-4o", "gpt-5", "gpt-4.1",
    "medium", "omni", "multimodal",
)


def build_ssl_context() -> ssl.SSLContext:
    """Return an SSL context with a working CA bundle.

    Python from python.org on macOS ships without one until you run its
    ``Install Certificates.command``; ``certifi`` (already a dependency of this
    project) supplies a bundle in the meantime. Verification is never disabled.
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def read_env() -> dict[str, str]:
    """Parse ``backend/.env`` into a dict. A missing file is not an error."""
    values: dict[str, str] = {}
    if not ENV_PATH.is_file():
        return values
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def fetch(url: str, headers: dict[str, str]) -> dict:
    """GET a JSON document, raising a readable error on failure."""
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30, context=build_ssl_context()) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()[:200]
        raise RuntimeError(f"HTTP {exc.code} — {body}") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ssl.SSLError):
            raise RuntimeError(
                f"TLS verification failed ({exc.reason}). Run `pip install certifi`, "
                "or your Python's 'Install Certificates.command' on macOS."
            ) from exc
        raise RuntimeError(f"could not reach {urllib.parse.urlparse(url).netloc}: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"response was not JSON: {exc}") from exc


def list_models(provider: str, api_key: str, endpoint: str, style: str) -> list[str]:
    """Return the model IDs a provider currently serves."""
    if style == "google":
        payload = fetch(f"{endpoint}?key={urllib.parse.quote(api_key)}", {})
        # Google returns "models/gemini-2.5-flash"; strip the prefix.
        return sorted(
            str(m.get("name", "")).removeprefix("models/")
            for m in payload.get("models", [])
        )
    if style == "anthropic":
        payload = fetch(
            endpoint, {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        )
        return sorted(str(m.get("id", "")) for m in payload.get("data", []))

    payload = fetch(endpoint, {"Authorization": f"Bearer {api_key}"})
    return sorted(str(m.get("id", "")) for m in payload.get("data", []))


def looks_multimodal(model_id: str) -> bool:
    """Heuristic guess at vision capability from the model ID."""
    lowered = model_id.lower()
    return any(hint in lowered for hint in VISION_HINTS)


def main(argv: list[str]) -> int:
    """Print each configured provider's model list. Returns an exit code."""
    show_all = "--all" in argv
    wanted = [a for a in argv if not a.startswith("-")]

    env = read_env()
    targets = {k: v for k, v in PROVIDERS.items() if not wanted or k in wanted}

    if wanted and not targets:
        print(f"  Unknown provider(s): {', '.join(wanted)}")
        print(f"  Choose from: {', '.join(PROVIDERS)}")
        return 1

    print("=" * 72)
    print("  Models available to your configured providers")
    print("=" * 72)

    contacted = 0
    for provider, (env_key, default_endpoint, style) in targets.items():
        api_key = env.get(env_key, "").strip()
        if not api_key:
            print(f"\n  {provider:<11} skipped — {env_key} not set in backend/.env")
            continue

        endpoint = default_endpoint
        if (override_var := BASE_URL_VARS.get(provider)) and (base := env.get(override_var, "").strip()):
            endpoint = f"{base.rstrip('/')}/models"

        contacted += 1
        print(f"\n  {provider}  ({endpoint})")
        try:
            models = list_models(provider, api_key, endpoint, style)
        except RuntimeError as exc:
            print(f"    FAILED: {exc}")
            continue

        if not models:
            print("    (provider returned no models)")
            continue

        shown = models if show_all else [m for m in models if looks_multimodal(m)]
        if not shown:
            print(f"    No obviously multimodal IDs among {len(models)} models.")
            print("    Re-run with --all to see everything.")
            continue

        for model_id in shown:
            print(f"    {model_id}")
        if not show_all:
            print(f"    ({len(shown)} likely-multimodal of {len(models)} total; --all shows everything)")

    if not contacted:
        print("\n  No providers configured. Add an API key to backend/.env first.")
        return 1

    print(
        "\n  Paste an ID into backend/.env as GEMINI_MODEL / CLAUDE_MODEL /\n"
        "  OPENAI_MODEL / GROQ_MODEL / SAMBANOVA_MODEL / MISTRAL_MODEL,\n"
        "  then restart the backend. The filter above is a name-based guess --\n"
        "  confirm vision support in the provider's docs before relying on it.\n"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print("\n  Cancelled.")
        sys.exit(130)
