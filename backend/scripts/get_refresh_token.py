#!/usr/bin/env python3
"""One-command Zoho Books setup: refresh token + organization ID.

Zoho's OAuth is the fiddliest part of this project. This script walks the whole
thing end to end and writes the results into ``backend/.env`` for you:

    1. Exchange an authorization code for a permanent refresh token.
    2. Use it to fetch an access token.
    3. Call /organizations to discover your ZOHO_ORGANIZATION_ID.
    4. List your expense accounts so you can pick ZOHO_DEFAULT_EXPENSE_ACCOUNT.

Two flows are supported, because Zoho's API console offers two client types and
they are NOT interchangeable:

* **Self Client** (default, recommended) -- a backend-only client. You generate
  the authorization code inside the console itself, so there is no browser
  redirect and no redirect URI to match. The token exchange must NOT include
  ``redirect_uri``; sending one produces ``invalid_code``.

* **Server-based Application** (``--browser``) -- the classic consent flow. This
  script starts a throwaway local HTTP server on your redirect URI to catch the
  code the instant Zoho sends it, because the code expires in about a minute and
  copying it out of a URL bar by hand usually loses that race.

Usage
-----
    cd backend
    python scripts/get_refresh_token.py               # Self Client flow
    python scripts/get_refresh_token.py --browser     # Server-based flow
    python scripts/get_refresh_token.py --fullaccess  # request every Books scope

Scopes are baked into the refresh token, so widening them means running this
again -- editing .env is not enough.

Uses only the standard library, so it runs before ``pip install``.
"""

from __future__ import annotations

import json
import ssl
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = BACKEND_ROOT / ".env"

DEFAULT_ACCOUNTS_DOMAIN = "https://accounts.zoho.in"
DEFAULT_REDIRECT_URI = "http://localhost:5173/zoho/callback"
DEFAULT_API_DOMAIN = "https://www.zohoapis.in"

# Creating an expense spans three Zoho modules. The one people miss is
# accountants.READ -- the chart of accounts is an accountant resource, not a
# settings one -- and its absence shows up only as the generic code 57
# "not authorized". Pass --fullaccess to request ZohoBooks.fullaccess.all
# instead if you would rather not debug scopes.
SCOPE = (
    "ZohoBooks.expenses.CREATE,"
    "ZohoBooks.expenses.READ,"
    "ZohoBooks.accountants.READ,"
    "ZohoBooks.settings.READ,"
    "ZohoBooks.contacts.READ"
)
FULL_SCOPE = "ZohoBooks.fullaccess.all"

_received: dict[str, str] = {}

SUCCESS_HTML = b"""<!doctype html><html><body style="font-family:system-ui;padding:3rem">
<h2>Authorisation received</h2><p>You can close this tab and return to your terminal.</p>
</body></html>"""

FAILURE_HTML = b"""<!doctype html><html><body style="font-family:system-ui;padding:3rem">
<h2>No authorisation code found</h2><p>Check the terminal for details.</p>
</body></html>"""


# ---------------------------------------------------------------------------
# TLS
# ---------------------------------------------------------------------------


def build_ssl_context() -> ssl.SSLContext:
    """Return an SSL context with a working CA bundle.

    Python installed from python.org on macOS does not use the system keychain
    and ships with no CA bundle until you run its ``Install Certificates.command``.
    Until then every HTTPS call from the standard library fails with
    ``CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate``.

    ``certifi`` is already present in this project (httpx depends on it), so we
    use its bundle when available and fall back to the system default otherwise.
    Verification is never disabled -- we are exchanging a client secret over
    this connection.
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _tls_help() -> str:
    """Actionable advice for a certificate verification failure."""
    return (
        "\n  TLS certificate verification failed. Your Python has no CA bundle.\n"
        "  This is standard on a python.org install for macOS. Fix it with ONE of:\n\n"
        "    1. Run the certificate installer that shipped with Python:\n"
        '         open "/Applications/Python 3.12/Install Certificates.command"\n'
        "       (adjust the version to match `python3 --version`)\n\n"
        "    2. Install certifi into the environment running this script:\n"
        "         pip install certifi\n"
        "       This script picks it up automatically.\n\n"
        "    3. Use Homebrew's Python instead, which trusts the system keychain:\n"
        "         brew install python@3.12\n"
    )


# ---------------------------------------------------------------------------
# .env helpers
# ---------------------------------------------------------------------------


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


def write_env_value(key: str, value: str) -> None:
    """Insert or update one key in ``backend/.env``, preserving everything else."""
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.is_file() else []
    for index, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[index] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def prompt(label: str, existing: str = "") -> str:
    """Ask for a value, reusing the existing one when present."""
    if existing:
        return existing
    while True:
        if value := input(f"{label}: ").strip():
            return value
        print("  This value is required.")


def confirm(question: str) -> bool:
    """Yes/no prompt defaulting to yes."""
    return input(f"  {question} [Y/n] ").strip().lower() in {"", "y", "yes"}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def post_form(url: str, data: dict[str, str]) -> dict[str, Any]:
    """POST url-encoded form data and return the parsed JSON body."""
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(data).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    return _send(request, url)


def get_json(url: str, access_token: str) -> dict[str, Any]:
    """Authenticated GET against the Books API."""
    request = urllib.request.Request(
        url, headers={"Authorization": f"Zoho-oauthtoken {access_token}"}
    )
    return _send(request, url)


def _send(request: urllib.request.Request, url: str) -> dict[str, Any]:
    """Execute a request and normalise every failure into ``{"error": ...}``."""
    try:
        with urllib.request.urlopen(request, timeout=30, context=build_ssl_context()) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}: {exc.read().decode()[:400]}"}
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ssl.SSLError):
            return {"error": f"{exc.reason}", "tls": True}
        return {"error": f"Could not reach {urllib.parse.urlparse(url).netloc}: {exc.reason}"}
    except ssl.SSLError as exc:
        return {"error": str(exc), "tls": True}
    except json.JSONDecodeError as exc:
        return {"error": f"Response was not JSON: {exc}"}


# ---------------------------------------------------------------------------
# Browser-flow callback server
# ---------------------------------------------------------------------------


class _CallbackHandler(BaseHTTPRequestHandler):
    """Captures the ``?code=`` Zoho appends to the redirect URI."""

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if code := params.get("code", [None])[0]:
            _received["code"] = code
            self.send_response(200)
            body = SUCCESS_HTML
        else:
            _received["error"] = params.get("error", ["no code in callback"])[0]
            self.send_response(400)
            body = FAILURE_HTML
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        """Silence per-request logging."""


def capture_code(redirect_uri: str, timeout: int = 300) -> str | None:
    """Serve the redirect path once and return the captured code."""
    parsed = urllib.parse.urlparse(redirect_uri)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    try:
        server = HTTPServer((host, port), _CallbackHandler)
    except OSError as exc:
        print(f"\n  Could not listen on {host}:{port} -- {exc}")
        print("  Something else is using that port (your Vite dev server, most likely).")
        print("  Stop it and re-run, or paste the code manually below.")
        return None

    server.timeout = timeout
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    thread.join(timeout)
    server.server_close()
    return _received.get("code")


# ---------------------------------------------------------------------------
# Code acquisition
# ---------------------------------------------------------------------------


def code_via_self_client(accounts_domain: str) -> str:
    """Walk the user through generating a code in the Zoho API console."""
    console = accounts_domain.replace("accounts.", "api-console.")
    print("\n  --- Self Client flow ---------------------------------------------")
    print(f"\n  1. Open {console}")
    print("  2. Click your Self Client, then the GENERATE CODE tab.")
    print("  3. Paste this into the Scope box:\n")
    print(f"       {SCOPE}\n")
    print("  4. Set Time Duration to 10 minutes, type any description, click CREATE.")
    print("  5. Pick your Zoho Books portal if it asks, then copy the generated code.\n")
    print("  The code is single-use and expires when the timer runs out, so paste it")
    print("  here as soon as you have it.\n")
    return input("  Paste the generated code: ").strip()


def code_via_browser(accounts_domain: str, client_id: str, redirect_uri: str) -> str:
    """Run the consent flow and capture the code from the redirect."""
    print("\n  --- Server-based Application flow --------------------------------")
    print(f"\n  Redirect URI: {redirect_uri}")
    print("  This must match your client's registration EXACTLY, including port.\n")

    auth_url = f"{accounts_domain}/oauth/v2/auth?" + urllib.parse.urlencode(
        {
            "scope": SCOPE,
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            # Both are required for Zoho to return a refresh token at all.
            "access_type": "offline",
            "prompt": "consent",
        }
    )

    print("  Opening your browser. If nothing happens, paste this URL yourself:\n")
    print(f"  {auth_url}\n")
    try:
        webbrowser.open(auth_url)
    except Exception as exc:  # noqa: BLE001 - headless machines have no browser
        print(f"  (Could not launch a browser automatically: {exc})")

    print("  Waiting for the Zoho redirect (up to 5 minutes)...")
    if code := capture_code(redirect_uri):
        return code

    print("\n  Automatic capture did not work.")
    print("  After approving, your browser lands on a URL containing '?code=...'.")
    return input("  Paste the code value here: ").strip()


# ---------------------------------------------------------------------------
# Post-token discovery
# ---------------------------------------------------------------------------


def discover_organization(api_domain: str, access_token: str) -> str | None:
    """List the user's Zoho Books organisations and return the chosen ID.

    This is the whole reason the script keeps going after the refresh token:
    ZOHO_ORGANIZATION_ID is otherwise a number you have to go hunting for in the
    Zoho Books UI, and pasting the wrong one produces a confusing 401.
    """
    print("\n  Looking up your Zoho Books organisations...")
    payload = get_json(f"{api_domain}/books/v3/organizations", access_token)

    if "error" in payload:
        print(f"    Could not list organisations: {payload['error']}")
        print("    Find it manually: Zoho Books -> gear icon -> Organisation Profile.")
        return None

    orgs = payload.get("organizations") or []
    if not orgs:
        print("    No organisations found. Have you finished Zoho Books signup?")
        return None

    if len(orgs) == 1:
        org = orgs[0]
        print(f"    Found: {org.get('name')}  (ID {org.get('organization_id')})")
        return str(org.get("organization_id"))

    print(f"    Found {len(orgs)} organisations:\n")
    for index, org in enumerate(orgs, start=1):
        default = " [default]" if org.get("is_default_org") else ""
        print(f"      {index}. {org.get('name')}  ID {org.get('organization_id')}{default}")

    while True:
        choice = input(f"\n    Which one? [1-{len(orgs)}] ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(orgs):
            return str(orgs[int(choice) - 1].get("organization_id"))
        print("    Please enter one of the listed numbers.")


def show_expense_accounts(api_domain: str, access_token: str, org_id: str) -> None:
    """Print expense account names so the user can set the default correctly."""
    print("\n  Fetching your expense accounts...")
    payload = get_json(
        f"{api_domain}/books/v3/chartofaccounts?organization_id={org_id}", access_token
    )
    if "error" in payload:
        print(f"    Could not list accounts: {payload['error']}")
        return

    accounts = [
        a
        for a in payload.get("chartofaccounts", [])
        if "expense" in str(a.get("account_type", "")).lower()
    ]
    if not accounts:
        print("    No expense accounts found. Check Accountant -> Chart of Accounts.")
        return

    print("    ZOHO_DEFAULT_EXPENSE_ACCOUNT must be one of these, spelled exactly:\n")
    for account in accounts[:20]:
        print(f"      {account.get('account_name')}")
    if len(accounts) > 20:
        print(f"      ... and {len(accounts) - 20} more")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    """Run the interactive flow. Returns a process exit code."""
    use_browser = "--browser" in argv

    print("=" * 72)
    print("  Zoho Books setup -- refresh token + organization ID")
    print("=" * 72)

    env = read_env()

    # Precedence: --fullaccess flag, then ZOHO_SCOPE in .env, then the narrow
    # default. The .env path matters because that is what the code-57 error
    # message tells people to do.
    global SCOPE
    if "--fullaccess" in argv:
        SCOPE = FULL_SCOPE
    elif env.get("ZOHO_SCOPE", "").strip():
        SCOPE = env["ZOHO_SCOPE"].strip()

    client_id = prompt("ZOHO_CLIENT_ID", env.get("ZOHO_CLIENT_ID", ""))
    client_secret = prompt("ZOHO_CLIENT_SECRET", env.get("ZOHO_CLIENT_SECRET", ""))
    redirect_uri = env.get("ZOHO_REDIRECT_URI") or DEFAULT_REDIRECT_URI
    accounts_domain = env.get("ZOHO_ACCOUNTS_DOMAIN") or DEFAULT_ACCOUNTS_DOMAIN

    print(f"\n  Scope           : {SCOPE}")
    print(f"  Client ID       : {client_id[:18]}...")
    print(f"  Accounts domain : {accounts_domain}")
    print(f"  Flow            : {'Server-based (browser)' if use_browser else 'Self Client'}")
    if not use_browser:
        print("\n  (Registered a Server-based Application instead? Re-run with --browser.)")

    code = (
        code_via_browser(accounts_domain, client_id, redirect_uri)
        if use_browser
        else code_via_self_client(accounts_domain)
    )
    if not code:
        print("\n  No authorization code. Nothing to exchange.")
        return 1

    # The Self Client flow has no redirect URI; sending one is rejected.
    data = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
    }
    if use_browser:
        data["redirect_uri"] = redirect_uri

    print("\n  Exchanging code for tokens...")
    payload = post_form(f"{accounts_domain}/oauth/v2/token", data)

    if "error" in payload:
        print(f"\n  FAILED: {payload['error']}")
        if payload.get("tls"):
            # A TLS failure never reached Zoho, so the OAuth advice below would
            # send the user hunting for a problem that is not there.
            print(_tls_help())
            return 1
        print(
            "\n  Most common causes:\n"
            "    * The code expired. Generate a fresh one and paste it immediately.\n"
            "    * The code was already used. Each one works exactly once.\n"
            "    * Wrong flow. A Self Client code must be exchanged WITHOUT a\n"
            "      redirect_uri; a Server-based code must be exchanged WITH the\n"
            "      exact registered one. Try the other mode (--browser toggles it).\n"
            "    * Wrong data centre: an .in account must use accounts.zoho.in."
        )
        return 1

    refresh_token = payload.get("refresh_token")
    access_token = payload.get("access_token", "")
    api_domain = str(payload.get("api_domain") or DEFAULT_API_DOMAIN).rstrip("/")

    if not refresh_token:
        print(f"\n  No refresh_token in the response: {payload}")
        print("  For the browser flow, re-authorise with prompt=consent.")
        print("  For a Self Client, make sure you generated a fresh code.")
        return 1

    print("\n" + "=" * 72)
    print("  SUCCESS")
    print("=" * 72)
    print(f"\n  ZOHO_REFRESH_TOKEN={refresh_token}")
    print(f"  Zoho reports api_domain: {api_domain}")
    print(f"  So ZOHO_BOOKS_BASE_URL should be {api_domain}/books/v3\n")

    write_refresh = confirm("Write ZOHO_REFRESH_TOKEN into backend/.env now?")
    if write_refresh:
        write_env_value("ZOHO_REFRESH_TOKEN", str(refresh_token))
        write_env_value("ZOHO_BOOKS_BASE_URL", f"{api_domain}/books/v3")
        print(f"  Written to {ENV_PATH}")
    else:
        print("  Not written. Copy the line above into backend/.env yourself.")

    # ------------------------------------------------------------ org lookup
    org_id = discover_organization(api_domain, access_token) if access_token else None

    if org_id:
        print(f"\n  ZOHO_ORGANIZATION_ID={org_id}")
        if confirm("Write ZOHO_ORGANIZATION_ID into backend/.env now?"):
            write_env_value("ZOHO_ORGANIZATION_ID", org_id)
            print(f"  Written to {ENV_PATH}")
        show_expense_accounts(api_domain, access_token, org_id)

    print("\n  Done. Restart the backend, then check:")
    print("      curl http://localhost:8000/api/zoho/status\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print("\n  Cancelled.")
        sys.exit(130)
