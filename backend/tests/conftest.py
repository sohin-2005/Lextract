"""Pytest bootstrap: make the Lextract suite hermetic.

Imported by pytest *before* any test module, and therefore before anything
imports ``app.database`` and instantiates the engine. That ordering is the whole
point of this file: it lets the suite run on a fresh clone with no ``.env``, no
API keys, no PostgreSQL and no network.

Without it, importing a service module would build a Postgres engine from the
default DSN and fail on a missing asyncpg driver -- a confusing error for
someone whose first act after cloning is to run the tests.
"""

from __future__ import annotations

import os

# Never read the developer's real backend/.env. A test that picked up live
# credentials could make a real, billable API call against a real account --
# and would also fail confusingly depending on which keys happen to be filled in.
os.environ["TAXOR_DISABLE_DOTENV"] = "1"

# Point at in-memory SQLite before app.database is imported. See
# ``app.database._engine_options``: SQLite is served by StaticPool, which
# rejects the Postgres pool arguments.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

# Two placeholder keys satisfy the "at least 2 providers" check in Settings.
# They are never used to make a call: every test that touches a provider points
# it at a local fake server instead.
os.environ.setdefault("GOOGLE_API_KEY", "test-key-not-real")
os.environ.setdefault("GROQ_API_KEY", "test-key-not-real")

# Uploads go to a temp dir so a test run never writes into dataset/bills.
os.environ.setdefault("UPLOAD_DIR", "/tmp/lextract-test-uploads")

# A proxy in the environment would intercept traffic to the loopback fakes.
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
for _proxy_var in (
    "ALL_PROXY",
    "all_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "HTTPS_PROXY",
    "https_proxy",
):
    os.environ.pop(_proxy_var, None)
