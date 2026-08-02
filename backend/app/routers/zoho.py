"""Zoho Books OAuth and expense-creation endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, status

from app.dependencies import AppSettings, DBSession
from app.schemas import (
    ZohoAuthUrlResponse,
    ZohoCallbackResponse,
    ZohoExpenseCreate,
    ZohoExpenseResponse,
    ZohoStatusResponse,
)
from app.services.zoho_service import (
    ZOHO_SCOPE,
    ZohoAPIError,
    ZohoAuthError,
    ZohoBooksService,
    ZohoNotConfiguredError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/zoho", tags=["zoho"])


@router.get("/status", response_model=ZohoStatusResponse, summary="Are Zoho credentials present?")
async def zoho_status(settings: AppSettings) -> ZohoStatusResponse:
    """Report configuration state so the UI can disable the sync button cleanly
    instead of letting the user click into a 500."""
    service = ZohoBooksService(settings)
    return ZohoStatusResponse(
        configured=settings.zoho_configured,
        organization_id=settings.zoho_organization_id,
        books_base_url=settings.zoho_books_base_url,
        missing=service.missing_settings(),
    )


@router.get("/auth-url", response_model=ZohoAuthUrlResponse, summary="Step 1 of the OAuth flow")
async def zoho_auth_url(settings: AppSettings) -> ZohoAuthUrlResponse:
    """Build the consent URL to open in a browser."""
    service = ZohoBooksService(settings)
    try:
        url = service.build_authorization_url()
    except ZohoNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return ZohoAuthUrlResponse(
        authorization_url=url,
        scope=ZOHO_SCOPE,
        instructions=(
            "Open this URL, approve access, and Zoho will redirect to your "
            "ZOHO_REDIRECT_URI with a ?code= parameter. Send that code to "
            "GET /api/zoho/callback?code=... within about 60 seconds -- the code "
            "is single-use and expires fast. Running "
            "`python scripts/get_refresh_token.py` does all of this for you."
        ),
    )


@router.get("/callback", response_model=ZohoCallbackResponse, summary="Step 2 of the OAuth flow")
async def zoho_callback(
    settings: AppSettings,
    code: str = Query(..., description="The one-time authorization code from Zoho."),
    self_client: bool = Query(
        False,
        description=(
            "Set true when the code came from the API console's Generate Code "
            "tab. Self Client exchanges must omit redirect_uri; sending one is "
            "rejected as 'invalid_code'."
        ),
    ),
) -> ZohoCallbackResponse:
    """Exchange the authorization code for a refresh token.

    The refresh token is returned to the caller and logged at INFO, *not*
    written to ``.env`` automatically. A web request that mutates the server's
    own credential file is a bad pattern, and in any case the process would have
    to restart to pick it up.
    """
    service = ZohoBooksService(settings)
    try:
        payload = await service.exchange_code_for_tokens(code, self_client=self_client)
    except (ZohoNotConfiguredError, ZohoAuthError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    refresh_token = str(payload.get("refresh_token", "")) or None
    if refresh_token:
        logger.info("Obtained Zoho refresh token (…%s).", refresh_token[-6:])

    return ZohoCallbackResponse(
        refresh_token=refresh_token,
        api_domain=payload.get("api_domain"),
        message=(
            "Paste this value into backend/.env as ZOHO_REFRESH_TOKEN, then restart "
            "the backend. It does not expire, so you only do this once."
        ),
    )


@router.get("/diagnose", summary="Find out which Zoho permission is blocking you")
async def zoho_diagnose(settings: AppSettings) -> dict[str, object]:
    """Walk the permission chain and report where it breaks.

    Zoho answers every permission problem with the same code 57, so a failed
    expense push cannot tell you which grant is missing. This probes each step
    separately. Read-only: it never creates an expense.
    """
    service = ZohoBooksService(settings)
    steps = await service.diagnose()
    blocked = next((s for s in steps if not s["ok"]), None)

    return {
        "healthy": blocked is None,
        "steps": steps,
        "blocked_at": blocked["step"] if blocked else None,
        "next_action": (
            "Everything the expense push depends on is working."
            if blocked is None
            else blocked["detail"]
        ),
    }


@router.get("/organizations", summary="Discover your ZOHO_ORGANIZATION_ID")
async def zoho_organizations(settings: AppSettings) -> dict[str, object]:
    """List the Zoho Books organisations your refresh token can reach.

    Use this to find ``ZOHO_ORGANIZATION_ID`` without digging through the Books
    UI. Only needs the client credentials and refresh token to be set.
    """
    service = ZohoBooksService(settings)
    try:
        organizations = await service.list_organizations()
    except ZohoNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except ZohoAuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except ZohoAPIError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return {
        "organizations": [
            {
                "organization_id": org.get("organization_id"),
                "name": org.get("name"),
                "currency_code": org.get("currency_code"),
                "is_default": org.get("is_default_org", False),
            }
            for org in organizations
        ],
        "hint": "Copy organization_id into ZOHO_ORGANIZATION_ID in backend/.env.",
    }


@router.post("/refresh", summary="Force an access-token refresh")
async def zoho_refresh(settings: AppSettings) -> dict[str, object]:
    """Exchange the refresh token for a fresh access token.

    Diagnostic endpoint: the service refreshes on demand during normal use. The
    access token itself is never returned -- only whether the exchange worked --
    so a working credential cannot be exfiltrated through this route.
    """
    service = ZohoBooksService(settings)
    try:
        token = await service.get_access_token(force_refresh=True)
    except (ZohoNotConfiguredError, ZohoAuthError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return {
        "status": "ok",
        "message": "Access token refreshed successfully.",
        "token_suffix": token[-6:],
    }


@router.post(
    "/expenses",
    response_model=ZohoExpenseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a Zoho Books expense from an extraction",
)
async def create_zoho_expense(
    payload: ZohoExpenseCreate, session: DBSession, settings: AppSettings
) -> ZohoExpenseResponse:
    """Push one extraction result into Zoho Books as an expense."""
    service = ZohoBooksService(settings)
    try:
        mapping = await service.create_expense(
            session,
            payload.extraction_result_id,
            account_name=payload.account_name,
            description=payload.description,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except ZohoNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except ZohoAuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except ZohoAPIError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return ZohoExpenseResponse.model_validate(mapping)
