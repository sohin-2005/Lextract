"""Tests for the Zoho Books OAuth and discovery paths.

A local HTTP server stands in for Zoho's accounts and Books APIs, so these
tests assert the exact form fields we send without needing a Zoho trial account.

The case that matters most is ``test_self_client_omits_redirect_uri``. Zoho's
API console offers two client types, and a Self Client -- the one the docs steer
you toward for a backend integration -- has no redirect URI. Sending one anyway
is rejected as ``invalid_code``, which points the user at the code rather than
at the real problem. That mistake costs an afternoon; this test costs 40 ms.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.parse
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from app.config import Settings
from app.services.zoho_service import ZohoAuthError, ZohoBooksService

_state: dict[str, Any] = {"calls": []}

ORGANIZATIONS = [
    {
        "organization_id": "60011234567",
        "name": "Test Trading Co",
        "currency_code": "INR",
        "is_default_org": True,
    },
    {
        "organization_id": "60019999999",
        "name": "Second Org",
        "currency_code": "INR",
        "is_default_org": False,
    },
]


class _FakeZohoHandler(BaseHTTPRequestHandler):
    """Stands in for accounts.zoho.in and the Books v3 API."""

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        length = int(self.headers.get("Content-Length", 0))
        form = {
            key: value[0]
            for key, value in urllib.parse.parse_qs(self.rfile.read(length).decode()).items()
        }
        _state["calls"].append({"method": "POST", "path": self.path, "form": form})

        # Mirror Zoho's real behaviour: a Self Client code sent with a
        # redirect_uri is rejected, and the error names the code, not the URI.
        if form.get("code") == "SELF-CLIENT-CODE" and "redirect_uri" in form:
            self._respond({"error": "invalid_code"})
            return

        self._respond(
            {
                "access_token": "AT-123",
                "refresh_token": "RT-456",
                "api_domain": "https://www.zohoapis.in",
                "expires_in": 3600,
                "token_type": "Bearer",
            }
        )

    def do_GET(self) -> None:  # noqa: N802
        _state["calls"].append(
            {"method": "GET", "path": self.path, "auth": self.headers.get("Authorization")}
        )
        self._respond({"code": 0, "message": "success", "organizations": ORGANIZATIONS})

    def _respond(self, obj: dict[str, Any]) -> None:
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        """Silence per-request logging."""


@pytest.fixture(scope="module")
def fake_zoho() -> Iterator[str]:
    """Run the fake Zoho on an ephemeral port; yield its origin."""
    server = HTTPServer(("127.0.0.1", 0), _FakeZohoHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address
    os.environ["NO_PROXY"] = "127.0.0.1,localhost"
    for var in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        os.environ.pop(var, None)
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture()
def service(fake_zoho: str) -> ZohoBooksService:
    """Service wired to the fake Zoho, with a cleared token cache."""
    _state["calls"] = []
    ZohoBooksService._token_cache = None
    ZohoBooksService._account_cache = {}
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        google_api_key="k",
        groq_api_key="k",
        zoho_client_id="1000.TESTCLIENTID",
        zoho_client_secret="test-secret",
        zoho_refresh_token="RT-456",
        zoho_organization_id="60011234567",
        zoho_accounts_domain=fake_zoho,
        zoho_books_base_url=f"{fake_zoho}/books/v3",
        zoho_redirect_uri="http://localhost:5173/zoho/callback",
    )
    return ZohoBooksService(settings)


# ---------------------------------------------------------------------------
# Token exchange
# ---------------------------------------------------------------------------


class TestCodeExchange:
    @pytest.mark.asyncio
    async def test_self_client_omits_redirect_uri(self, service: ZohoBooksService) -> None:
        """A Self Client has no redirect URI, so we must not send one."""
        payload = await service.exchange_code_for_tokens("SELF-CLIENT-CODE", self_client=True)

        assert payload["refresh_token"] == "RT-456"
        assert "redirect_uri" not in _state["calls"][0]["form"]

    @pytest.mark.asyncio
    async def test_self_client_code_with_redirect_uri_is_rejected(
        self, service: ZohoBooksService
    ) -> None:
        """Guards the failure mode this flag exists to prevent."""
        with pytest.raises(ZohoAuthError, match="invalid_code"):
            await service.exchange_code_for_tokens("SELF-CLIENT-CODE", self_client=False)

    @pytest.mark.asyncio
    async def test_browser_flow_sends_redirect_uri(self, service: ZohoBooksService) -> None:
        """The server-based flow requires the exact registered redirect URI."""
        payload = await service.exchange_code_for_tokens("WEB-CODE", self_client=False)

        assert payload["refresh_token"] == "RT-456"
        assert _state["calls"][0]["form"]["redirect_uri"] == "http://localhost:5173/zoho/callback"

    @pytest.mark.asyncio
    async def test_grant_type_and_credentials_present(self, service: ZohoBooksService) -> None:
        await service.exchange_code_for_tokens("WEB-CODE")
        form = _state["calls"][0]["form"]

        assert form["grant_type"] == "authorization_code"
        assert form["client_id"] == "1000.TESTCLIENTID"
        assert form["client_secret"] == "test-secret"


# ---------------------------------------------------------------------------
# Authorisation URL
# ---------------------------------------------------------------------------


class TestAuthorizationUrl:
    def test_requests_offline_access_and_consent(self, service: ZohoBooksService) -> None:
        """Without both of these Zoho returns no refresh token at all, and the
        integration dies silently one hour later."""
        query = urllib.parse.parse_qs(
            urllib.parse.urlparse(service.build_authorization_url()).query
        )

        assert query["access_type"] == ["offline"]
        assert query["prompt"] == ["consent"]
        assert query["response_type"] == ["code"]

    def test_includes_books_scopes(self, service: ZohoBooksService) -> None:
        query = urllib.parse.parse_qs(
            urllib.parse.urlparse(service.build_authorization_url()).query
        )
        assert "ZohoBooks.expenses.CREATE" in query["scope"][0]


# ---------------------------------------------------------------------------
# Access-token caching
# ---------------------------------------------------------------------------


class TestAccessToken:
    @pytest.mark.asyncio
    async def test_token_is_cached_across_calls(self, service: ZohoBooksService) -> None:
        """A burst of expense pushes must cost one token call, not N."""
        first = await service.get_access_token()
        second = await service.get_access_token()

        assert first == second == "AT-123"
        token_calls = [c for c in _state["calls"] if c["method"] == "POST"]
        assert len(token_calls) == 1

    @pytest.mark.asyncio
    async def test_force_refresh_bypasses_cache(self, service: ZohoBooksService) -> None:
        await service.get_access_token()
        await service.get_access_token(force_refresh=True)

        token_calls = [c for c in _state["calls"] if c["method"] == "POST"]
        assert len(token_calls) == 2


# ---------------------------------------------------------------------------
# Organisation discovery
# ---------------------------------------------------------------------------


class TestOrganizationDiscovery:
    @pytest.mark.asyncio
    async def test_lists_organizations(self, service: ZohoBooksService) -> None:
        organizations = await service.list_organizations()

        assert len(organizations) == 2
        assert organizations[0]["organization_id"] == "60011234567"

    @pytest.mark.asyncio
    async def test_uses_zoho_oauthtoken_scheme(self, service: ZohoBooksService) -> None:
        """Zoho uses `Zoho-oauthtoken <token>`, not `Bearer <token>`."""
        await service.list_organizations()
        get_call = next(c for c in _state["calls"] if c["method"] == "GET")

        assert get_call["auth"] == "Zoho-oauthtoken AT-123"

    @pytest.mark.asyncio
    async def test_does_not_send_organization_id(self, service: ZohoBooksService) -> None:
        """/organizations is the one Books call that takes no organization_id --
        its entire purpose is to tell you what yours is."""
        await service.list_organizations()
        get_call = next(c for c in _state["calls"] if c["method"] == "GET")

        assert get_call["path"].endswith("/organizations")
        assert "organization_id" not in get_call["path"]


# ---------------------------------------------------------------------------
# Configuration reporting
# ---------------------------------------------------------------------------


class TestConfiguration:
    def test_missing_settings_are_named(self, fake_zoho: str) -> None:
        """The UI shows this list, so it has to name real env vars."""
        settings = Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            google_api_key="k",
            groq_api_key="k",
            zoho_client_id="1000.CID",
        )
        missing = ZohoBooksService(settings).missing_settings()

        assert "ZOHO_CLIENT_SECRET" in missing
        assert "ZOHO_REFRESH_TOKEN" in missing
        assert "ZOHO_ORGANIZATION_ID" in missing
        assert "ZOHO_CLIENT_ID" not in missing
        assert settings.zoho_configured is False
