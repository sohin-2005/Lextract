"""Zoho Books integration: OAuth2 plus expense creation.

Zoho's OAuth is the fiddliest part of this project, so the shape of it is worth
stating plainly:

1. A **Self Client** in the Zoho API Console gives you a client id + secret.
2. You send the user to an authorization URL once; Zoho redirects back with a
   short-lived ``code``.
3. You exchange that ``code`` for a **refresh token**, which never expires.
4. From then on you swap the refresh token for a 1-hour **access token** on
   demand. Only step 4 happens at request time.

The refresh token is the durable secret and lives in ``.env``. Access tokens
are cached in memory with a 60-second safety margin, so a burst of expense
pushes costs one token call, not N.

**Data-centre matters.** ``accounts.zoho.in`` issues tokens that only work
against ``zohoapis.in``. Mixing ``.com`` and ``.in`` is the single most common
Zoho integration failure and produces a misleading "invalid token" error. Both
domains are configurable in ``.env``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import ExtractionResult, ZohoExpenseMapping
from app.utils.constants import SyncStatus

logger = logging.getLogger(__name__)

# Creating an expense touches three modules, and Zoho scopes them separately:
#   expenses.CREATE     -- POST /expenses
#   accountants.READ    -- GET /chartofaccounts  (chart of accounts is an
#                          *accountant* resource, not a settings one -- this is
#                          the scope most commonly missed, and its absence
#                          surfaces as the generic "not authorized" code 57)
#   settings.READ       -- GET /organizations
# Narrow scopes are better practice, but if you would rather not think about it,
# set ZOHO_SCOPE=ZohoBooks.fullaccess.all in .env and move on.
ZOHO_SCOPE = (
    "ZohoBooks.expenses.CREATE,"
    "ZohoBooks.expenses.READ,"
    "ZohoBooks.accountants.READ,"
    "ZohoBooks.settings.READ,"
    "ZohoBooks.contacts.READ"
)
_TOKEN_SAFETY_MARGIN_SECONDS = 60
_HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class ZohoError(RuntimeError):
    """Base class for Zoho failures."""


class ZohoNotConfiguredError(ZohoError):
    """Credentials are missing from the environment."""


class ZohoAuthError(ZohoError):
    """Token exchange or refresh was rejected."""


class ZohoAPIError(ZohoError):
    """The Books API returned a non-success response."""

    def __init__(self, message: str, *, status_code: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


@dataclass(slots=True)
class _CachedToken:
    """An access token and the wall-clock time it stops being usable."""

    value: str
    expires_at: float

    @property
    def is_valid(self) -> bool:
        return bool(self.value) and time.time() < self.expires_at


class ZohoBooksService:
    """Thin, well-behaved client over the Zoho Books v3 REST API."""

    # Class-level so every request shares one cached access token.
    _token_cache: _CachedToken | None = None
    _token_lock: asyncio.Lock = asyncio.Lock()
    _account_cache: dict[str, str] = {}

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    # -------------------------------------------------------------- OAuth 2
    def build_authorization_url(self, *, scope: str | None = None) -> str:
        """Return the consent URL the user opens once, by hand.

        ``access_type=offline`` and ``prompt=consent`` together are what make
        Zoho return a refresh token. Omit either and you get an access token
        only, and the integration silently dies after an hour.
        """
        if not self._settings.zoho_client_id:
            raise ZohoNotConfiguredError("ZOHO_CLIENT_ID is not set in backend/.env.")
        scope = scope or self._settings.zoho_scope.strip() or ZOHO_SCOPE
        query = urlencode(
            {
                "scope": scope,
                "client_id": self._settings.zoho_client_id,
                "response_type": "code",
                "redirect_uri": self._settings.zoho_redirect_uri,
                "access_type": "offline",
                "prompt": "consent",
            }
        )
        return f"{self._settings.zoho_accounts_domain}/oauth/v2/auth?{query}"

    async def exchange_code_for_tokens(
        self, code: str, *, self_client: bool = False
    ) -> dict[str, Any]:
        """Trade the one-time ``code`` for a refresh token.

        Args:
            code: The single-use authorization code.
            self_client: ``True`` when the code came from the API console's
                **Generate Code** tab rather than a browser redirect. Self
                Clients have no redirect URI, and Zoho rejects the exchange if
                one is sent -- confusingly, with ``invalid_code`` rather than a
                message about the redirect. This is the single most common way
                the Zoho setup fails, hence the explicit flag.

        Raises:
            ZohoAuthError: If Zoho rejects the code. Codes are single-use and
                expire in minutes, so "invalid_code" usually means it was
                already spent, it timed out, or the wrong flow was used.
        """
        self._require(("zoho_client_id", "zoho_client_secret"))
        data = {
            "grant_type": "authorization_code",
            "client_id": self._settings.zoho_client_id,
            "client_secret": self._settings.zoho_client_secret,
            "code": code,
        }
        if not self_client:
            data["redirect_uri"] = self._settings.zoho_redirect_uri
        payload = await self._post_token(data)
        if "refresh_token" not in payload:
            hint = (
                "Generate a fresh code from the console's Generate Code tab."
                if self_client
                else "Re-run the consent step with access_type=offline and "
                "prompt=consent, and check the redirect URI matches your "
                "registration character for character."
            )
            raise ZohoAuthError(
                f"Zoho did not return a refresh_token. {hint} Zoho said: {payload}"
            )
        return payload

    async def list_organizations(self) -> list[dict[str, Any]]:
        """Return the Zoho Books organisations this token can see.

        Exists so ``ZOHO_ORGANIZATION_ID`` can be discovered rather than hunted
        for in the Books UI. Note this endpoint is deliberately called *without*
        an ``organization_id`` query parameter -- it is the one Books call that
        does not take one, since its whole purpose is to tell you what yours is.
        """
        self._require(("zoho_client_id", "zoho_client_secret", "zoho_refresh_token"))
        token = await self.get_access_token()
        url = f"{self._settings.zoho_books_base_url.rstrip('/')}/organizations"
        headers = {"Authorization": f"Zoho-oauthtoken {token}"}

        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                response = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise ZohoAPIError(f"Could not reach Zoho Books: {exc}") from exc

        try:
            payload = response.json()
        except ValueError:
            raise ZohoAPIError(
                f"Zoho Books returned non-JSON (HTTP {response.status_code}).",
                status_code=response.status_code,
            ) from None

        if response.status_code >= 400 or payload.get("code", 0) not in (0, None):
            raise ZohoAPIError(
                f"Zoho Books error: {payload.get('message', 'unknown error')}",
                status_code=response.status_code,
                payload=payload,
            )
        return list(payload.get("organizations") or [])

    async def get_access_token(self, *, force_refresh: bool = False) -> str:
        """Return a valid access token, refreshing at most once concurrently."""
        if not force_refresh and (cached := type(self)._token_cache) and cached.is_valid:
            return cached.value

        async with type(self)._token_lock:
            # Re-check: another coroutine may have refreshed while we waited.
            if not force_refresh and (cached := type(self)._token_cache) and cached.is_valid:
                return cached.value

            self._require(("zoho_client_id", "zoho_client_secret", "zoho_refresh_token"))
            payload = await self._post_token(
                {
                    "grant_type": "refresh_token",
                    "client_id": self._settings.zoho_client_id,
                    "client_secret": self._settings.zoho_client_secret,
                    "refresh_token": self._settings.zoho_refresh_token,
                }
            )
            token = payload.get("access_token")
            if not token:
                raise ZohoAuthError(f"No access_token in Zoho response: {payload}")

            expires_in = int(payload.get("expires_in", 3600))
            type(self)._token_cache = _CachedToken(
                value=token,
                expires_at=time.time() + expires_in - _TOKEN_SAFETY_MARGIN_SECONDS,
            )
            logger.info("Refreshed Zoho access token (valid %ss).", expires_in)
            return token

    async def _post_token(self, data: dict[str, Any]) -> dict[str, Any]:
        """POST to the token endpoint and normalise Zoho's error shapes."""
        url = f"{self._settings.zoho_accounts_domain}/oauth/v2/token"
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                response = await client.post(url, data=data)
        except httpx.HTTPError as exc:
            raise ZohoAuthError(f"Could not reach Zoho accounts server: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise ZohoAuthError(
                f"Zoho returned non-JSON (HTTP {response.status_code}): {response.text[:300]}"
            ) from exc

        # Zoho returns HTTP 200 with {"error": "..."} on failure.
        if response.status_code >= 400 or "error" in payload:
            raise ZohoAuthError(
                f"Zoho OAuth error (HTTP {response.status_code}): {payload.get('error', payload)}"
            )
        return payload

    # ------------------------------------------------------------ Books API
    async def _request(
        self, method: str, path: str, *, operation: str | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        """Authenticated Books API call with one automatic token retry.

        Args:
            operation: Human-readable name used in error messages. Without it,
                a failure in the account lookup and a failure in the expense
                POST are indistinguishable to the user -- and they have
                completely different fixes.
        """
        label = operation or f"{method} {path}"
        self._require(("zoho_organization_id",))
        url = f"{self._settings.zoho_books_base_url.rstrip('/')}/{path.lstrip('/')}"
        params = {"organization_id": self._settings.zoho_organization_id, **kwargs.pop("params", {})}

        for attempt in (1, 2):
            token = await self.get_access_token(force_refresh=attempt == 2)
            headers = {
                "Authorization": f"Zoho-oauthtoken {token}",
                "X-com-zoho-books-organizationid": str(self._settings.zoho_organization_id),
            }
            try:
                async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                    response = await client.request(
                        method, url, headers=headers, params=params, **kwargs
                    )
            except httpx.HTTPError as exc:
                raise ZohoAPIError(f"Could not reach Zoho Books: {exc}") from exc

            if response.status_code == 401 and attempt == 1:
                logger.info("Zoho returned 401; forcing a token refresh and retrying once.")
                continue

            try:
                payload = response.json()
            except ValueError:
                raise ZohoAPIError(
                    f"Zoho Books returned non-JSON (HTTP {response.status_code}).",
                    status_code=response.status_code,
                    payload=response.text[:500],
                ) from None

            # Books uses its own {"code": 0} success marker on top of HTTP status.
            if response.status_code >= 400 or payload.get("code", 0) not in (0, None):
                raise ZohoAPIError(
                    self._explain_api_error(label, payload, response.status_code),
                    status_code=response.status_code,
                    payload=payload,
                )
            return payload

        raise ZohoAPIError("Zoho Books authentication failed after a token refresh.")

    @staticmethod
    def _explain_missing_account(cause: str) -> str:
        """Explain how to proceed when the chart-of-accounts lookup is blocked.

        Worth being explicit that pinning the id is a complete fix rather than a
        workaround: the lookup exists only to turn a friendly name into an id,
        so supplying the id removes the need for the scope altogether.
        """
        return (
            f"Could not resolve the expense account. {cause}\n\n"
            "Zoho's expense API needs an account *id*; the name is not accepted. "
            "Two ways forward:\n"
            "  A. Pin the id and skip the lookup entirely (no extra scope needed):\n"
            "     In Zoho Books go to Accountant -> Chart of Accounts, click your "
            "expense account, and copy the long number from the browser URL. Put it "
            "in backend/.env as ZOHO_EXPENSE_ACCOUNT_ID, then restart.\n"
            "  B. Re-authorise with a wider scope so the lookup works:\n"
            "     set ZOHO_SCOPE=ZohoBooks.fullaccess.all in backend/.env, then run\n"
            "     python scripts/get_refresh_token.py"
        )

    @staticmethod
    def _explain_api_error(operation: str, payload: dict[str, Any], status: int) -> str:
        """Turn Zoho's terse error codes into something actionable.

        Code 57 in particular is Zoho's catch-all for "your token is valid but
        not allowed to do this", which sends people to re-check credentials that
        are perfectly fine. The real cause is almost always a missing OAuth
        scope, and the scope needed depends on which call failed -- hence
        ``operation``.
        """
        code = payload.get("code")
        message = str(payload.get("message", "unknown error"))

        if code == 57 or "not authorized" in message.lower():
            return (
                f"Zoho refused '{operation}' (code 57: {message}). The token is valid "
                "but lacks permission. In order of likelihood:\n"
                "  1. Missing OAuth scope. Scopes are baked into the refresh token, so "
                "adding one means regenerating it: re-run "
                "`python scripts/get_refresh_token.py`. The chart-of-accounts lookup "
                "needs ZohoBooks.accountants.READ, which is easy to omit. Simplest "
                "fix: set ZOHO_SCOPE=ZohoBooks.fullaccess.all in .env first.\n"
                "  2. The Zoho user who authorised the token is not an Admin of this "
                "organisation.\n"
                "  3. Wrong ZOHO_ORGANIZATION_ID -- the token is valid but for a "
                "different org. Check GET /api/zoho/organizations."
            )
        if "expense account" in message.lower():
            return (
                f"Zoho rejected the expense account (during '{operation}': {message}). "
                "The id sent does not name an account Zoho will book an expense to. "
                "Check ZOHO_EXPENSE_ACCOUNT_ID points at an account whose type is an "
                "expense account -- Zoho Books -> Accountant -> Chart of Accounts, open "
                "the account, and copy the number from the URL. Income and asset "
                "accounts are rejected here."
            )
        if code == 4 or "invalid" in message.lower() and status == 401:
            return f"Zoho rejected the token during '{operation}': {message}"
        return f"Zoho Books error during '{operation}': {message}"

    async def resolve_account_id(self, account_name: str, *, expense: bool = True) -> str | None:
        """Map a chart-of-accounts *name* to the *id* the API actually wants.

        The brief's payload uses ``account_name``, but Zoho Books requires
        ``account_id``. Rather than making the user hunt for an opaque 19-digit
        id, we look it up by name and cache the mapping for the process
        lifetime. Returns ``None`` if no account matches, and the caller decides
        whether that is fatal.
        """
        cache_key = f"{'exp' if expense else 'pay'}:{account_name.casefold()}"
        if cached := type(self)._account_cache.get(cache_key):
            return cached

        payload = await self._request(
            "GET", "/chartofaccounts", operation="look up the chart of accounts"
        )
        accounts = payload.get("chartofaccounts", []) or []
        wanted = account_name.strip().casefold()

        for account in accounts:
            if str(account.get("account_name", "")).strip().casefold() == wanted:
                account_id = str(account.get("account_id"))
                type(self)._account_cache[cache_key] = account_id
                return account_id

        available = ", ".join(str(a.get("account_name")) for a in accounts[:15])
        logger.warning(
            "Zoho account %r not found. First accounts in this org: %s", account_name, available
        )
        return None

    async def create_expense(
        self,
        session: AsyncSession,
        extraction_result_id: uuid.UUID,
        *,
        account_name: str | None = None,
        description: str | None = None,
    ) -> ZohoExpenseMapping:
        """Push one extraction to Zoho Books as an expense.

        The mapping row is written whether the push succeeds or fails, so a
        failed sync is visible in the UI with its error message rather than
        vanishing. Re-pushing an already-synced result is refused rather than
        silently creating a duplicate expense in the user's books.

        Raises:
            ZohoError: On any configuration, auth or API failure.
            ValueError: If the extraction is unusable (no amount, or failed).
        """
        result = await session.get(ExtractionResult, extraction_result_id)
        if result is None:
            raise ValueError(f"Extraction result {extraction_result_id} does not exist.")
        if not result.succeeded:
            raise ValueError("Cannot push a failed extraction to Zoho Books.")
        if result.amount is None:
            raise ValueError(
                "Extraction has no amount. Zoho Books requires a numeric amount, so "
                "correct the extraction (or use ground truth) before syncing."
            )

        existing = result.zoho_mapping
        if existing is not None and existing.sync_status == SyncStatus.SYNCED:
            raise ValueError(
                f"Already synced to Zoho as expense {existing.zoho_expense_id}. "
                "Delete it in Zoho Books first if you need to re-push."
            )

        if not self._settings.zoho_configured:
            raise ZohoNotConfiguredError(
                "Zoho Books is not configured. Fill in ZOHO_CLIENT_ID, "
                "ZOHO_CLIENT_SECRET, ZOHO_REFRESH_TOKEN and ZOHO_ORGANIZATION_ID in "
                "backend/.env. See README section 'Zoho Books setup'."
            )

        target_account = account_name or self._settings.zoho_default_expense_account

        # Zoho's expense API takes account_id, not account_name. Three ways to
        # get one, in order of robustness:
        #   1. ZOHO_EXPENSE_ACCOUNT_ID, pinned in .env. Needs no extra scope,
        #      which matters because the lookup below is the *only* thing
        #      requiring ZohoBooks.accountants.READ.
        #   2. Resolve the configured name via /chartofaccounts.
        #   3. Give up and say precisely how to do (1).
        account_id: str | None = self._settings.zoho_expense_account_id.strip() or None

        if account_id is None:
            try:
                account_id = await self.resolve_account_id(target_account)
            except ZohoAPIError as exc:
                raise ZohoAPIError(self._explain_missing_account(str(exc))) from exc

            if account_id is None:
                raise ZohoAPIError(
                    f"No expense account named {target_account!r} exists in this Zoho "
                    "organisation. Either set ZOHO_DEFAULT_EXPENSE_ACCOUNT to a name "
                    "from Zoho Books -> Accountant -> Chart of Accounts, or pin the id "
                    "directly with ZOHO_EXPENSE_ACCOUNT_ID."
                )

        expense_date = (result.date or result.created_at.date()).isoformat()
        payload: dict[str, Any] = {
            "account_id": account_id,
            "date": expense_date,
            "amount": float(result.amount),
            "currency_code": result.currency or "INR",
            "description": description
            or (
                f"Extracted from handwritten bill via AI - Model: {result.model_name}"
                + (f" - Vendor: {result.vendor_name}" if result.vendor_name else "")
            ),
        }
        if result.bill_number:
            payload["reference_number"] = result.bill_number[:100]

        mapping = existing or ZohoExpenseMapping(extraction_result_id=result.id)
        mapping.request_payload = json.dumps(payload, indent=2)

        try:
            response = await self._request(
                "POST", "/expenses", operation="create the expense", json=payload
            )
        except ZohoError as exc:
            mapping.sync_status = SyncStatus.FAILED
            mapping.error_message = str(exc)[:2000]
            session.add(mapping)
            await session.commit()
            await session.refresh(mapping)
            raise

        expense = response.get("expense", {}) or {}
        mapping.zoho_expense_id = str(expense.get("expense_id") or "") or None
        mapping.sync_status = SyncStatus.SYNCED
        mapping.error_message = None
        mapping.response_payload = json.dumps(response)[:20_000]
        session.add(mapping)
        await session.commit()
        await session.refresh(mapping)

        logger.info(
            "Created Zoho expense %s for extraction %s.", mapping.zoho_expense_id, result.id
        )
        return mapping

    async def diagnose(self) -> list[dict[str, Any]]:
        """Probe each permission the expense push needs, in dependency order.

        Zoho reports every permission problem as the same code 57, so a single
        failed push cannot tell you *which* grant is missing. This walks the
        chain one call at a time and reports where it stops -- turning a guess
        into a specific answer.

        Never raises: each step records its own failure so the whole report
        comes back even when the first step fails.
        """
        steps: list[dict[str, Any]] = []

        def record(name: str, scope: str, ok: bool, detail: str) -> None:
            steps.append({"step": name, "scope": scope, "ok": ok, "detail": detail})

        missing = self.missing_settings()
        if missing:
            record("Credentials present", "-", False, f"Missing from .env: {', '.join(missing)}")
            return steps
        record("Credentials present", "-", True, "All four Zoho settings are set.")

        try:
            await self.get_access_token(force_refresh=True)
            record("Exchange refresh token", "-", True, "Access token obtained.")
        except ZohoError as exc:
            record("Exchange refresh token", "-", False, str(exc))
            return steps

        try:
            organizations = await self.list_organizations()
            ids = [str(o.get("organization_id")) for o in organizations]
            configured = str(self._settings.zoho_organization_id)
            if configured in ids:
                record(
                    "List organisations",
                    "ZohoBooks.settings.READ",
                    True,
                    f"ZOHO_ORGANIZATION_ID {configured} matches your account.",
                )
            else:
                record(
                    "List organisations",
                    "ZohoBooks.settings.READ",
                    False,
                    f"ZOHO_ORGANIZATION_ID is {configured}, but this token can only "
                    f"see {ids or 'no organisations'}.",
                )
                return steps
        except ZohoError as exc:
            record("List organisations", "ZohoBooks.settings.READ", False, str(exc))
            return steps

        if pinned := self._settings.zoho_expense_account_id.strip():
            record(
                "Resolve expense account",
                "-",
                True,
                f"Using ZOHO_EXPENSE_ACCOUNT_ID={pinned}; the chart-of-accounts "
                "lookup is skipped, so accountants.READ is not needed.",
            )
            record("Create expense", "ZohoBooks.expenses.CREATE", True,
                   "Not probed -- it would create a real expense.")
            return steps

        wanted = self._settings.zoho_default_expense_account
        try:
            account_id = await self.resolve_account_id(wanted)
            if account_id:
                record(
                    "Read chart of accounts",
                    "ZohoBooks.accountants.READ",
                    True,
                    f"Found expense account {wanted!r} (id {account_id}).",
                )
            else:
                record(
                    "Read chart of accounts",
                    "ZohoBooks.accountants.READ",
                    False,
                    f"Scope is fine, but no account is named {wanted!r}. Set "
                    "ZOHO_DEFAULT_EXPENSE_ACCOUNT to a name from Zoho Books -> "
                    "Accountant -> Chart of Accounts.",
                )
        except ZohoError as exc:
            record("Read chart of accounts", "ZohoBooks.accountants.READ", False, str(exc))
            return steps

        record(
            "Create expense",
            "ZohoBooks.expenses.CREATE",
            True,
            "Not probed -- it would create a real expense. Everything it depends on passed.",
        )
        return steps

    # ------------------------------------------------------------- internal
    def _require(self, attributes: tuple[str, ...]) -> None:
        """Fail loudly, and specifically, on missing credentials."""
        env_names = {
            "zoho_client_id": "ZOHO_CLIENT_ID",
            "zoho_client_secret": "ZOHO_CLIENT_SECRET",
            "zoho_refresh_token": "ZOHO_REFRESH_TOKEN",
            "zoho_organization_id": "ZOHO_ORGANIZATION_ID",
        }
        missing = [env_names[a] for a in attributes if not getattr(self._settings, a, None)]
        if missing:
            raise ZohoNotConfiguredError(
                f"Missing Zoho settings: {', '.join(missing)}. Add them to backend/.env."
            )

    def missing_settings(self) -> list[str]:
        """Names of the env vars still required for a live Books call."""
        pairs = (
            ("ZOHO_CLIENT_ID", self._settings.zoho_client_id),
            ("ZOHO_CLIENT_SECRET", self._settings.zoho_client_secret),
            ("ZOHO_REFRESH_TOKEN", self._settings.zoho_refresh_token),
            ("ZOHO_ORGANIZATION_ID", self._settings.zoho_organization_id),
        )
        return [name for name, value in pairs if not (value and str(value).strip())]
