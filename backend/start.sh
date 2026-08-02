#!/usr/bin/env sh
# Container entrypoint.
#
# The obvious command is `alembic upgrade head && uvicorn …`, and it is a trap
# on a managed platform: if the migration fails — no DATABASE_URL, the database
# service not linked yet, a bad DSN — the `&&` means uvicorn never starts,
# nothing binds the port, and the platform reports only "service unavailable"
# with no way to reach /api/health and find out why.
#
# So: attempt the migration, report clearly, and start the server regardless.
# /api/health already degrades gracefully — it answers 200 with
# `database: "unavailable"` — which turns a silent crash loop into a page that
# tells you what is actually wrong.
set -u

PORT="${PORT:-8000}"

echo "──────────────────────────────────────────────"
echo " Lextract API"
echo "──────────────────────────────────────────────"
echo " port          : ${PORT}"
echo " database set  : $([ -n "${DATABASE_URL:-}" ] && echo yes || echo 'NO — set DATABASE_URL')"
echo " cors origins  : ${CORS_ORIGINS:-<default: localhost only>}"
echo " providers     : ${ENABLED_PROVIDERS:-<all>}"
echo "──────────────────────────────────────────────"

if [ -z "${DATABASE_URL:-}" ]; then
  echo "WARNING: DATABASE_URL is unset. Add a PostgreSQL service and reference it,"
  echo "         e.g. DATABASE_URL=\${{Postgres.DATABASE_URL}} on Railway."
fi

echo "Running database migrations…"
if alembic upgrade head; then
  echo "Migrations applied."
else
  echo "WARNING: migrations failed. Starting the API anyway so /api/health can"
  echo "         report the problem. Expect 500s on any endpoint touching the DB."
fi

echo "Starting uvicorn on 0.0.0.0:${PORT}"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
