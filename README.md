# Lextract

> **Extract. Compare. Evaluate. Powered by AI.**

Lextract is an AI-powered platform that extracts structured information from
handwritten receipts using multiple vision language models and benchmarks their
performance.

### → **[lextract-bay.vercel.app](https://lextract-bay.vercel.app)**

![Lextract dashboard](assets/01-dashboard.png)

> Hosted on free tiers. The API sleeps after ~15 minutes idle, so the first
> request can take 30–60 seconds to wake it — the dashboard will show
> "API offline" until it does. Reload once and it connects.
>
> | | |
> |---|---|
> | Frontend | https://lextract-bay.vercel.app |
> | API | https://lextract-7gvy.onrender.com |
> | API docs | https://lextract-7gvy.onrender.com/docs |
> | Health | https://lextract-7gvy.onrender.com/api/health |

Extract structured accounting data from photographs of **handwritten Indian
bills** using several vision LLMs, score every model against human ground truth
field by field, and push the result you trust into Zoho Books.

The interesting part is not the extraction — it is the **evaluation harness**.
Anyone can call a vision model. The question a finance team actually needs
answered is *which* model, at what accuracy, at what cost per hundred bills, and
how badly does it fail when it fails.

---

## Contents

- [What it does](#what-it-does)
- [Screenshots](#screenshots)
- [Architecture](#architecture)
- [Tech stack](#tech-stack)
- [Accounts you need](#accounts-you-need--what-is-actually-free)
- [Quick start](#quick-start)
- [Deploying](#deploying)
- [Usage flow](#usage-flow)
- [API reference](#api-reference)
- [Choosing which models to run](#choosing-which-models-to-run)
- [Evaluation methodology](#evaluation-methodology)
- [Results](#results)
- [Testing](#testing)
- [Project structure](#project-structure)
- [Troubleshooting](#troubleshooting)

---

## What it does

1. **Upload** a photo of a handwritten bill (drag-and-drop, or the API).
2. **Extract** six fields — vendor name, bill number, date, amount, currency,
   tax/GST — running the enabled models concurrently. Eight providers are
   implemented; which ones appear is one line of config.
3. **Record ground truth** by reading the bill yourself.
4. **Score** each model per field with partial credit for near-misses, because
   `Sharma Genral Store` is not the same kind of wrong as `Verma Medicals`.
5. **Compare** accuracy, latency and cost per 100 bills on one leaderboard.
6. **Sync** the extraction you trust to Zoho Books as an expense.

---

## Screenshots

**Dashboard** — headline metrics, upload zone and model picker. Exactly one
card carries the accent; the moment two do, neither reads as the primary
number.

![Dashboard](assets/01-dashboard.png)

**Receipts** — every upload with its status, how many extraction runs it has,
and whether ground truth exists yet. Run count matters: results are an
append-only log, so a re-run adds a row rather than overwriting one.

![Receipt list](assets/02-receipts.png)

**Leaderboard** — per-field accuracy, latency and cost extrapolated to 100
bills, with a plain-English recommendation that refuses to declare a winner on
a sample too small to support one.

![Model leaderboard](assets/03-leaderboard.png)

**Comparison grid** — the original receipt beside every model's reading. Rows
are fields, columns are models: reading *across* a row shows which fields are
hard for every model, which is the more actionable question than which model
is best overall.

![Bill detail with model comparison](assets/04-comparison.png)

**Ground truth and Zoho** — the answer key is typed against the image, then the
extraction you trust is pushed to Zoho Books as an expense.

![Ground truth entry and Zoho push](assets/05-ground-truth.png)

**Disagreements** — where models differ, at least one is wrong. These fields
are flagged *before* any ground truth exists, so they are the first place to
look on a fresh dataset. Raw responses are kept underneath for auditing.

![Model disagreements and raw responses](assets/06-disagreements.png)

**Zoho Books sync** — the created expense id is stored, so a re-push is
refused rather than silently duplicating the expense in your books.

![Expense synced to Zoho Books](assets/07-zoho-synced.png)

---

## Architecture

```
┌───────────────────────────────────────────────────────────────────────┐
│  React 18 + Vite + Tailwind          http://localhost:5173            │
│                                                                       │
│  Dashboard          BillDetail                                        │
│  ├ BillUploader     ├ ModelComparison   (fields × models grid)        │
│  ├ bill table       ├ EvaluationForm    (ground-truth entry)          │
│  └ leaderboard      ├ ExtractionResults (raw JSON + disagreements)    │
│    (recharts)       └ ZohoExpenseCreator                              │
└───────────────────────────────┬───────────────────────────────────────┘
                                │  axios · /api proxied by Vite
┌───────────────────────────────▼───────────────────────────────────────┐
│  FastAPI (async)                     http://localhost:8000            │
│                                                                       │
│  routers/                                                             │
│    bills.py       upload · list · detail · image · delete             │
│    extraction.py  run models · read results                           │
│    evaluation.py  ground truth · score · aggregate report             │
│    zoho.py        oauth · expense creation                            │
│                              │                                        │
│  services/                   ▼                                        │
│    llm_clients.py   UnifiedLLMClient (ABC)                            │
│                     ├── GeminiClient ──────► Google   google-genai    │
│                     ├── ClaudeClient ──────► Anthropic  anthropic     │
│                     └── OpenAICompatibleClient  (shared wire format)  │
│                         ├── OpenAIClient ──► OpenAI                   │
│                         ├── GroqClient ────► Groq                     │
│                         ├── SambaNovaClient ► SambaNova               │
│                         ├── MistralClient ─► Mistral                  │
│                         ├── NvidiaClient ──► NVIDIA NIM               │
│                         ├── MoonshotClient ► Moonshot / Kimi          │
│                         └── OpenRouterClient ► OpenRouter (any lab)   │
│    extractors.py    concurrent orchestration · type coercion          │
│    evaluator.py     ★ scoring rubric (pure, no I/O, unit-tested)      │
│    zoho_service.py  OAuth2 · token cache · Books v3 ──► Zoho Books    │
│                              │                                        │
│  utils/                      ▼                                        │
│    image_proc.py    magic-byte validation · downscale for cost        │
│    constants.py     prompt · pricing · thresholds                     │
└───────────────────────────────┬───────────────────────────────────────┘
                                │  SQLAlchemy 2.0 async · asyncpg
┌───────────────────────────────▼───────────────────────────────────────┐
│  PostgreSQL 15                     Local filesystem                   │
│                                                                       │
│  bills ──┬─ extraction_results ──┬─ evaluation_scores                 │
│          │                       └─ zoho_expense_mappings             │
│          └─ ground_truths                    dataset/bills/*.jpg      │
│  All primary keys are UUIDs.                                          │
└───────────────────────────────────────────────────────────────────────┘
```

**Why async throughout.** Every request either waits on Postgres or on a remote
vision model. Pairing `async def` endpoints with a *synchronous* driver would
block the event loop on each query and silently serialise concurrent
extractions, so the stack is asyncpg end to end and Alembic runs its migrations
inside `asyncio.run`.

**Why results are append-only.** Re-running extraction always INSERTs new rows.
A later run can never silently rewrite numbers a reviewer already looked at.

**Why one client for four providers.** Groq, SambaNova and Mistral all expose
the same OpenAI `/chat/completions` contract with the same multimodal message
shape. They differ only by base URL and two optional parameter names, so they
share one implementation — adding a seventh OpenAI-compatible provider is a
subclass with two class attributes.

---

## Tech stack

| Layer | Choice | Version |
|---|---|---|
| Language | Python | 3.11+ (3.12 recommended) |
| API | FastAPI | ≥ 0.115 |
| ORM | SQLAlchemy (async) | ≥ 2.0.36 |
| Migrations | Alembic | ≥ 1.14 |
| Validation | Pydantic + pydantic-settings | v2 |
| Database | PostgreSQL | 15+ |
| DB driver | asyncpg | ≥ 0.30 |
| Fuzzy matching | thefuzz + python-Levenshtein | ≥ 0.22 |
| Images | Pillow | ≥ 10.4 |
| Frontend | React | 18.3 |
| Build | Vite | 6 |
| Styling | Tailwind CSS (class-based dark mode) | 3.4 |
| HTTP | Axios | 1.7 |
| Charts | Recharts | 2.15 |
| Icons | lucide-react | 0.469 |
| Type | Inter + JetBrains Mono | Google Fonts |

### Design system

Built from the logo, not chosen separately:

| Role | Hex | Use |
|---|---|---|
| Ink | `#14181C` | Wordmark, mark, body text |
| Accent | `#0EA47E` | Scores, CTAs, active state, meters |
| Paper | `#F2F0E9` | App surface, icon tile |
| Muted | `#6A6E72` | Tagline, secondary labels |

Light mode is ink on paper; dark mode inverts to paper on ink, matching the two
logo variants. Every foreground/background pair clears WCAG AA in both modes —
CTAs are ink on teal (5.63:1) rather than white on teal (3.17:1, which fails).

---

## Accounts you need — what is *actually* free

You need **any two** of the eight LLM providers for a comparison to mean
anything. The backend refuses to start with fewer (override with
`ALLOW_SINGLE_PROVIDER=true`).

**Five of the seven are free with no credit card.** You can run this entire
project, five models wide, for $0.

| Service | Model used | Cost reality | Card required |
|---|---|---|---|
| **PostgreSQL 15** | — | Free — or `docker compose up -d db` | No |
| **Google AI Studio** | `gemini-2.5-flash` | **Free tier** | **No** |
| **Groq** | `qwen/qwen3.6-27b` | **Free tier** | **No** |
| **SambaNova Cloud** | `Llama-4-Maverick-17B-128E` | **Free tier** | **No** |
| **Mistral AI** | `pixtral-12b` | **Free tier** | **No** |
| **Moonshot AI** | `kimi-k2.6` | Trial credits | No |
| **OpenRouter** | any, e.g. `moonshotai/kimi-k2.6` | Pay-as-you-go + free models | No |
| **NVIDIA NIM** | `llama-3.2-11b-vision` | **Free credits** | **No** |
| **Anthropic** | `claude-haiku-4-5` | Paid, ~$5 minimum | Yes |
| **OpenAI** | `gpt-5-mini` | Paid, ~$5 minimum | Yes |
| **Zoho Books** | — | Free 14-day trial | No |

Rate limits on the free tiers are per-minute, not per-month, and a 15-bill run
is well inside them.

> **Important:** a **Claude Pro subscription does not include API credits.**
> claude.ai and the Anthropic API are billed separately. Having Pro does not
> give you an `ANTHROPIC_API_KEY` with credit on it — you top up the API account
> at `console.anthropic.com` independently. The same is true of ChatGPT Plus
> versus the OpenAI API.

**Recommended free path: Gemini + Groq + SambaNova + Mistral + NVIDIA.** Five
vision models, five different labs, no card, no spend — and a more interesting
comparison than the original three, because it puts open weights (Llama 4,
Llama 3.2, Qwen, Pixtral) against closed ones, and runs two different Llama
generations side by side.

If you also want the paid two, a single $5 top-up each covers this project many
times over: a full 15-bill run costs well under $0.10 on these model tiers.

### Google AI Studio — free, do this first

1. Go to **https://aistudio.google.com/app/apikey**
2. Sign in with any Google account
3. **Create API key** → copy it
4. Paste into `backend/.env` as `GOOGLE_API_KEY=...`

No billing setup, no card.

### Groq — free, no card

1. Go to **https://console.groq.com/keys**
2. Sign in → **Create API Key** → copy it
3. Paste into `backend/.env` as `GROQ_API_KEY=...`

Groq's proposition is raw inference speed, so it is the one to watch in the
`avg_latency_ms` column.

### SambaNova Cloud — free, no card

1. Go to **https://cloud.sambanova.ai/apis**
2. Sign up → generate an API key → copy it
3. Paste into `backend/.env` as `SAMBANOVA_API_KEY=...`

### Mistral AI — free, no card

1. Go to **https://console.mistral.ai/api-keys**
2. Sign up → subscribe to the free **Experiment** tier → **Create new key**
3. Paste into `backend/.env` as `MISTRAL_API_KEY=...`

Pixtral was trained with document understanding as a first-class goal rather
than as a side effect of general vision, which makes it a genuinely different
bet on handwritten bills.

### Moonshot AI (Kimi) — trial credits

1. Go to **https://platform.kimi.ai/console/api-keys**
2. Sign up → **Create API Key** → copy it
3. Paste into `backend/.env` as `MOONSHOT_API_KEY=...`

Default model is `kimi-k2.6`: vision-capable, has a non-thinking mode, and is
several times cheaper than the `kimi-k3` flagship ($3/$15 per Mtok with
always-on reasoning). Avoid `moonshot-v1-*-vision-preview` — those are being
sunset and are already unavailable to new accounts.

Kimi's training mix is much heavier on Chinese and English document data than
the other models here, which makes it a genuinely different bet on dense scans.

### OpenRouter — one key, most labs

1. Go to **https://openrouter.ai/keys** → **Create Key** → copy it
2. Paste into `backend/.env` as `OPENROUTER_API_KEY=...`
3. Pick a vision model at
   https://openrouter.ai/models?modality=text+image and set `OPENROUTER_MODEL`

Useful when a lab has no free tier of its own, or when you'd rather not manage
five accounts. Two caveats: OpenRouter adds a small margin and one network hop,
so a model benchmarked *through* OpenRouter is not strictly comparable on cost
or latency with the same model called directly.

> **Model IDs are namespaced.** OpenRouter says `moonshotai/kimi-k2.6`;
> Moonshot's own API says `kimi-k2.6`. Mixing up the key and the endpoint
> returns a bare `401 Invalid Authentication`. The client detects this and names
> the mismatch — see troubleshooting.

### NVIDIA NIM — free credits, no card

1. Go to **https://build.nvidia.com/**
2. Pick any vision model → **Get API Key** → sign in → copy it
3. Paste into `backend/.env` as `NVIDIA_API_KEY=...`

Serves Llama 3.2 Vision, an older and smaller generation than the Llama 4 that
SambaNova runs. Comparing the two on the same bills separates gains from model
scale from gains in training data.

> NVIDIA rejects oversized inline images rather than degrading them, so the
> client compresses to fit `NVIDIA_MAX_IMAGE_B64_BYTES` (default 180 KB). Raise
> it if your account allows larger payloads.

### Anthropic — paid, ~$5

1. Go to **https://console.anthropic.com/**
2. Sign up (separate from your claude.ai login)
3. **Plans & Billing** → add a payment method → buy credits (minimum ~$5)
4. **API Keys** → **Create Key** → copy it immediately, it is shown once
5. Paste into `backend/.env` as `ANTHROPIC_API_KEY=...`

### OpenAI — paid, ~$5

1. Go to **https://platform.openai.com/api-keys**
2. **Settings → Billing** → add a payment method and credits first — a key
   without credit returns a quota error on every call
3. **Create new secret key** → copy it immediately
4. Paste into `backend/.env` as `OPENAI_API_KEY=...`

### Zoho Books — optional, free 14-day trial

Everything except the sync button works without this. The endpoints return a
clear "not configured" message and the UI disables the button with the reason.

**Step 1 — Zoho Books account.** Sign up at https://www.zoho.com/in/books/ and
finish the org-creation wizard. You do not need to note anything down; step 4
discovers your organization ID for you.

**Step 2 — create an API client.** Go to https://api-console.zoho.in/ and pick
one of two types. They are *not* interchangeable:

| | **Self Client** (recommended) | **Server-based Application** |
|---|---|---|
| Redirect URI | none | required, must match exactly |
| How you get a code | console → **Generate Code** tab | browser consent redirect |
| Token exchange | must **omit** `redirect_uri` | must **include** `redirect_uri` |
| Script flag | *(default)* | `--browser` |

Self Client is simpler — no redirect URI to get wrong, no local server, no race
against the browser. Use it unless you need a real consent flow.

Copy the **Client ID** and **Client Secret** into `backend/.env`.

**Step 3 — get the refresh token and organization ID.**

```bash
cd backend
python scripts/get_refresh_token.py             # Self Client
python scripts/get_refresh_token.py --browser   # Server-based Application
```

The script prints the exact scope string to paste into the console, exchanges
the code, then uses the resulting token to look up your organization ID and list
your expense accounts. It offers to write `ZOHO_REFRESH_TOKEN`,
`ZOHO_ORGANIZATION_ID` and `ZOHO_BOOKS_BASE_URL` into `.env` for you.

The scope to paste when the console asks:

```
ZohoBooks.expenses.CREATE,ZohoBooks.expenses.READ,ZohoBooks.accountants.READ,ZohoBooks.settings.READ,ZohoBooks.contacts.READ
```

Or run `python scripts/get_refresh_token.py --fullaccess` for
`ZohoBooks.fullaccess.all` if you'd rather not debug scopes.

**Step 4 — verify.** Restart the backend, then:

```bash
curl http://localhost:8000/api/zoho/status          # should say configured: true
curl http://localhost:8000/api/zoho/organizations   # your org IDs
```

**Finding the organization ID by hand**, if you prefer: Zoho Books → gear icon →
*Organisation Profile*, or read the `organization_id` out of the URL after
logging in. `GET /api/zoho/organizations` does the same thing without leaving
the terminal.

**Step 5 — expense account.** `ZOHO_DEFAULT_EXPENSE_ACCOUNT` must match a name
in your chart of accounts *exactly*. The script prints the valid names; you can
also read them from Zoho Books → *Accountant* → *Chart of Accounts*.

> **Data-centre gotcha.** An account created on `zoho.in` issues tokens that
> only work against `zohoapis.in`. If you signed up on `zoho.com`, change *both*
> `ZOHO_ACCOUNTS_DOMAIN` and `ZOHO_BOOKS_BASE_URL` to the `.com` equivalents.
> Mismatched domains produce a misleading "invalid token" error. The script
> reads the correct API domain out of Zoho's own token response and writes it
> for you, which sidesteps this entirely.

---

## Quick start

### Prerequisites

```bash
python3 --version   # 3.11 or newer
node --version      # 18 or newer
psql --version      # 15 or newer
```

### 1. Database — pick one

**Option A: Docker (recommended).** No local Postgres, no role/permission
puzzles, and the credentials already match `.env.example`:

```bash
docker compose up -d db
```

**Option B: native Postgres.** Note the gotcha — Homebrew creates a role named
after your macOS user, *not* `postgres`, so the conventional
`postgresql://postgres:postgres@…` DSN fails with
`role "postgres" does not exist`. Either use your own username:

```bash
createdb lextract
# then in backend/.env:
#   DATABASE_URL=postgresql+asyncpg://$(whoami)@localhost:5432/lextract
```

…or create the role the DSN expects:

```bash
psql postgres -c "CREATE ROLE postgres LOGIN SUPERUSER PASSWORD 'postgres';"
createdb -O postgres lextract
```

### 2. Backend

```bash
cd backend

python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# Open backend/.env and paste at least two API keys (see the section above).

alembic upgrade head
uvicorn app.main:app --reload
```

Backend on **http://localhost:8000** · interactive docs at
**http://localhost:8000/docs**

Confirm it is wired up correctly:

```bash
curl http://localhost:8000/api/health
```

```json
{
  "status": "ok",
  "database": "connected",
  "configured_providers": ["gemini", "claude"],
  "models": { "gemini": "gemini-2.5-flash", "claude": "claude-haiku-4-5-20251001" },
  "zoho_configured": false
}
```

### 3. Frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Frontend on **http://localhost:5173**.

### Running the whole backend in Docker

Once Docker Desktop is installed and running:

```bash
cp backend/.env.example backend/.env      # paste your API keys into it
docker compose up --build
```

That starts PostgreSQL, waits for it to pass a health check, applies the
Alembic migrations, and serves the API on **http://localhost:8000**. The
frontend still runs natively (`cd frontend && npm run dev`) — Vite's hot reload
is worth more than containerising it, and its proxy already points at
`localhost:8000`.

| Command | Effect |
|---|---|
| `docker compose up -d db` | Just PostgreSQL; run the backend natively |
| `docker compose up --build` | PostgreSQL + backend, migrations included |
| `docker compose logs -f backend` | Tail the API logs |
| `docker compose down` | Stop everything, keep the data |
| `docker compose down -v` | Stop **and delete** the database volume |

`dataset/` is bind-mounted, so uploaded bills land on your machine and survive
`docker compose down`. `DATABASE_URL` is overridden inside the container to
point at the `db` service, so the value in your `.env` only affects native runs.

---

## Deploying

The live instance runs the frontend on Vercel and the API on Render:
**https://lextract-bay.vercel.app** → **https://lextract-7gvy.onrender.com**.


The Vite dev proxy is a **dev-server feature**. It does not exist in a built
bundle, so a deployed frontend must be told where the backend lives.

### Frontend (Vercel)

| Setting | Value |
|---|---|
| Environment variable | `VITE_API_BASE_URL` = `https://<your-backend>.onrender.com` |
| Build command | `npm run build` |
| Output directory | `dist` |
| Root directory | `frontend` |

No trailing slash, no `/api` suffix. **Vite inlines this at build time**, so
changing it requires a redeploy — restarting is not enough.

`frontend/vercel.json` supplies the SPA fallback rewrite. Without it, opening
`/bills/<uuid>` directly or refreshing on it returns 404, because Vercel looks
for a file at that path.

### Backend (Railway)

**Set Root Directory to `backend`.** This is the one that catches people: the
repo root has no `requirements.txt`, no `Dockerfile` and no `package.json`, so
a builder pointed at the root cannot work out how to build anything and fails
in about two seconds with "Failed to build an image".

With the root directory set, `backend/railway.json` takes over: it pins the
Dockerfile builder and points the health check at `/api/health`. No start
command needed — the Dockerfile runs migrations then starts uvicorn on `$PORT`.

Add a PostgreSQL service, then set the variables below. Railway exposes its
database as `DATABASE_URL` in `postgresql://` form; the app rewrites it to
`postgresql+asyncpg://` automatically, so you can reference it directly:

```
DATABASE_URL      = ${{Postgres.DATABASE_URL}}
CORS_ORIGINS      = https://<your-app>.vercel.app
ENABLED_PROVIDERS = gemini,groq,nvidia
GOOGLE_API_KEY    = …
GROQ_API_KEY      = …
NVIDIA_API_KEY    = …
```

Do **not** set `PORT` yourself — Railway injects it.

### Backend (Render)

| Variable | Value |
|---|---|
| `CORS_ORIGINS` | `https://<your-app>.vercel.app` |
| `DATABASE_URL` | your managed Postgres URL |
| `UPLOAD_DIR` | a path on a **persistent disk** — see below |
| provider keys | `GOOGLE_API_KEY`, `GROQ_API_KEY`, … |
| `ENABLED_PROVIDERS` | e.g. `gemini,groq,nvidia` |

Start command:

```
alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Three things that bite on free tiers:

1. **CORS.** The default only allows `localhost`. Until `CORS_ORIGINS` names
   your Vercel origin, the browser blocks every response even though the
   backend answered fine.
2. **Cold starts.** Render free instances spin down after ~15 minutes idle;
   the next request takes 30–60 seconds. The frontend's timeout is generous
   enough to survive it, but the first load will feel broken.
3. **Ephemeral disk.** Render's free filesystem is wiped on every deploy and
   restart. With `UPLOAD_DIR` on it, uploaded receipts vanish while their
   database rows remain, and the image endpoint starts returning
   `410 Gone`. Attach a persistent disk and point `UPLOAD_DIR` at it, or treat
   the deployment as a demo and keep the real dataset local.

### Verifying

```bash
curl https://<your-backend>.onrender.com/api/health
```

Should return `configured_providers` and `database: connected`. If that works
but the site still fails, the problem is `VITE_API_BASE_URL` or `CORS_ORIGINS`,
not the backend.

## Usage flow

1. **Collect and redact 10–15 bills** — see [`dataset/README.md`](dataset/README.md).
   The redaction rules there are not optional; these images are sent to
   third-party APIs.
2. **Upload** them by dragging onto the dashboard.
3. **Select models** with the checkboxes (only providers with a key are
   enabled), then hit **Extract**. All selected models run concurrently.
4. **Open a bill.** The comparison grid shows every field × every model. Where
   models disagree, at least one is wrong — those fields are flagged.
5. **Enter ground truth** in the form beside the image. You may prefill from a
   model to save typing, but check every field against the photo — rubber-stamping
   a model's output means scoring it against itself.
6. **Evaluate.** Each cell gets a colour-coded score badge; hover for the reason.
7. **Repeat** across the dataset, then read the leaderboard on the dashboard.
8. **Push to Zoho** from the bill detail page, choosing the model you trust.

---

## API reference

All routes are under `/api`. Full interactive schema at `/docs`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | DB reachability + configured providers |
| `POST` | `/api/bills/upload` | `multipart/form-data` upload → `bill_id` |
| `GET` | `/api/bills` | List bills with counts |
| `GET` | `/api/bills/{id}` | Bill + extractions + ground truth |
| `GET` | `/api/bills/{id}/image` | Serve the stored image |
| `DELETE` | `/api/bills/{id}` | Delete bill and cascade |
| `POST` | `/api/extract/{bill_id}` | Body `{"models": ["gemini","groq","mistral"]}` |
| `GET` | `/api/extract/{bill_id}/results` | All extraction attempts |
| `DELETE` | `/api/extract/results/disabled` | Purge results from de-listed providers |
| `POST` | `/api/ground-truth/{bill_id}` | Create or correct the answer key |
| `GET` | `/api/ground-truth/{bill_id}` | Read the answer key |
| `POST` | `/api/evaluate/{bill_id}` | Score every extraction (idempotent) |
| `GET` | `/api/evaluation/report` | Cross-bill leaderboard |
| `GET` | `/api/zoho/status` | Are credentials present? |
| `GET` | `/api/zoho/auth-url` | OAuth step 1 |
| `GET` | `/api/zoho/callback?code=&self_client=` | OAuth step 2 → refresh token |
| `GET` | `/api/zoho/organizations` | Discover your `ZOHO_ORGANIZATION_ID` |
| `GET` | `/api/zoho/diagnose` | Pinpoint which Zoho permission is blocking |
| `POST` | `/api/zoho/refresh` | Force an access-token refresh |
| `POST` | `/api/zoho/expenses` | Create an expense from an extraction |

### End-to-end with curl

```bash
BILL=$(curl -s -F "file=@dataset/bills/bill_01_grocery.jpg" \
  http://localhost:8000/api/bills/upload | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")

curl -s -X POST http://localhost:8000/api/extract/$BILL \
  -H 'Content-Type: application/json' \
  -d '{"models":["gemini","groq","sambanova","mistral"]}'

curl -s -X POST http://localhost:8000/api/ground-truth/$BILL \
  -H 'Content-Type: application/json' \
  -d '{"vendor_name":"Sharma General Store","bill_number":null,
       "date":"2024-03-15","amount":"245.50","currency":"INR",
       "tax_gst_details":null}'

curl -s -X POST http://localhost:8000/api/evaluate/$BILL
curl -s http://localhost:8000/api/evaluation/report
```

---

## Choosing which models to run

`ENABLED_PROVIDERS` in `backend/.env` is an allowlist controlling what appears
in the UI, the API and the report:

```
ENABLED_PROVIDERS=gemini,groq,moonshot,nvidia
```

Every provider client stays in the codebase — this only decides which are
*offered*. Re-enabling one is a `.env` edit and a restart, not a code change,
and the API rejects a disabled provider rather than merely hiding it. Leave the
value blank to allow all eight.

Because extraction results are an append-only audit log, narrowing the list
hides a provider from the picker but leaves its old rows in the comparison grid
and the leaderboard. Delete them explicitly when you want a clean slate:

```bash
curl -X DELETE http://localhost:8000/api/extract/results/disabled
```

That is deliberately an explicit action rather than a silent filter on every
read — a report that quietly disagrees with the database is worse than one with
stale rows in it.

## Evaluation methodology

Full write-up: [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md). In short:

**Why field-level.** A single "correct / incorrect" verdict per bill throws away
the information you need. Knowing a model nails amounts but mangles vendor names
tells you to auto-post amounts and route names to review. A boolean tells you
nothing actionable.

**Why partial credit.** On handwriting, most errors are near-misses. Exact
matching scores a dropped letter and a completely wrong shop identically, which
misrepresents both.

| Outcome | Score | Label |
|---|---|---|
| Identical after normalisation | 1.0 | `exact` |
| `thefuzz.ratio` ≥ 90 | 0.9 | `fuzzy` |
| `thefuzz.ratio` ≥ 70 | 0.7 | `partial` |
| `thefuzz.ratio` < 70 | 0.0 | `missing` |
| Both null | 1.0 | `not_applicable` |
| Truth exists, model returned null | 0.0 | `missing` |
| Truth is null, model invented a value | 0.0 | `missing` (hallucination) |

**Type-aware exceptions.** Dates parse to `date` and compare semantically, so
`15/03/2024` and `2024-03-15` both score 1.0. Amounts parse to `Decimal` with a
1% tolerance band scoring 0.9. GSTINs compare exactly — a checksum identifier
with one wrong character is useless, so it must not earn fuzzy credit.

**Cost extrapolation.** `mean cost per bill × 100`. Deliberately naive, and it
*understates* real cost: it excludes retries and the human review pass.

---

## Results

Measured over 13 receipts, three models, `GET /api/evaluation/report`.

| Model | Overall | Vendor | Amount | Date | Avg latency | Cost / 100 bills | Bills |
|---|---|---|---|---|---|---|---|
| `gemini-3.5-flash` | **84.5%** | 84% | 77% | 92% | 4.12s | $0.32 | 13 |
| `qwen/qwen3.6-27b` (Groq) | 68.5% | 61% | 54% | 55% | 11.72s | $0.15 | 11 |
| `meta/llama-3.2-11b-vision-instruct` (NVIDIA) | 60.6% | 95% | 15% | 69% | 2.84s | $0.04 | 13 |

### What the numbers say

**Overall accuracy hides the useful finding.** Llama 3.2 ranks last at 60.6%,
yet it reads the **vendor name better than either other model** — 95% against
Gemini's 84%. It collapses on `amount` (15%), which is what drags its average
down. A single "which model is best" score would have buried that; the
field-level breakdown is the whole reason the harness exists.

That combination is also actionable: Llama is the cheapest and fastest of the
three, so a pipeline could plausibly take vendor names from it and amounts from
Gemini, at a fraction of running Gemini on everything.

**Amount is the hardest field across the board** — 77% / 54% / 15%. On these
receipts the total is usually the smallest, most-crowded handwriting on the
page, and models tend to grab a line item or the subtotal instead.

**Groq's latency is the outlier.** 11.72s against 2.84s for NVIDIA, on a
platform whose entire proposition is speed. Its vision model is a reasoning
model; even with thinking disabled it deliberates far longer per image than the
others.

**Treat the ranking cautiously.** 13 receipts puts the 95% confidence interval
at roughly ±15 points, so the 16-point gap between Gemini and Qwen is
suggestive but the 8-point gap between Qwen and Llama is not a ranking. The
report says so itself rather than printing a confident order that will not
replicate.

**Cost per 100 bills spans 8×** — $0.04 to $0.32 — while accuracy spans 24
points. Whether that trade is worth it depends entirely on whether a human
reviews the output; see
[METHODOLOGY §6](docs/METHODOLOGY.md#6-recommendation-framework).

### Before submitting

- [ ] `assets/*.png` replaced with real captures (see `assets/README.md`)
- [ ] `dataset/ground_truth.json` holds your answer keys, not the templates
- [ ] Every receipt in `dataset/bills/` is redacted — git history is permanent
- [ ] The results table above is filled in from `GET /api/evaluation/report`
- [ ] `VITE_API_BASE_URL` set on Vercel, `CORS_ORIGINS` set on Render
- [ ] `backend/.env` is **not** committed (`git ls-files | grep .env` → only `.env.example`)

## Testing

```bash
cd backend
source venv/bin/activate
pytest -v
```

162 tests across four suites, all hermetic — no database, no network, no API
keys. `tests/conftest.py` sets `TAXOR_DISABLE_DOTENV=1`, so the suite can never
read your real `.env` or make a billable call.

| Suite | Tests | Covers |
|---|---|---|
| `test_evaluator.py` | 69 | Exact/fuzzy/partial boundaries, day-first date parsing, amount tolerance, null vs. hallucination, GSTIN identity matching, aggregation, re-run deduplication |
| `test_llm_clients.py` | 69 | Per-provider wire format, JSON repair (extra braces, fences, prose, truncation), the one-shot retry, reasoning-model handling, cost estimation, the provider allowlist, 401 diagnostics |
| `test_zoho_service.py` | 12 | Self Client vs. server-based OAuth, token caching, organisation discovery |
| `conftest.py` | — | Test isolation |

Clone and run — it finishes in about twelve seconds:

```bash
cd backend && source venv/bin/activate && pytest
```

## Project structure

```
lextract/
├── assets/                       README screenshots
├── backend/
│   ├── app/
│   │   ├── main.py               FastAPI factory, CORS, exception handlers
│   │   ├── config.py             Settings, provider allowlist, ≥2-provider check
│   │   ├── database.py           Async engine, session factory, Base
│   │   ├── models.py             5 ORM models, UUID PKs
│   │   ├── schemas.py            Request/response contracts
│   │   ├── dependencies.py       DB session + settings injection
│   │   ├── routers/              bills · extraction · evaluation · zoho
│   │   ├── services/
│   │   │   ├── llm_clients.py    8 providers + JSON repair + retry
│   │   │   ├── extractors.py     Concurrent orchestration, type coercion
│   │   │   ├── evaluator.py      ★ scoring rubric (pure, no I/O)
│   │   │   └── zoho_service.py   OAuth2, token cache, Books v3
│   │   └── utils/                image_proc · constants (prompt, pricing)
│   ├── alembic/versions/         0001_initial_schema.py
│   ├── scripts/                  get_refresh_token.py · list_models.py
│   ├── tests/                    conftest · evaluator · llm_clients · zoho
│   └── requirements.txt · .env.example · Dockerfile · pytest.ini
├── frontend/
│   ├── src/
│   │   ├── App.jsx               Routing, header, splash gate, theme
│   │   ├── hooks/useTheme.js     Light/dark, persisted, OS-aware
│   │   ├── pages/                Dashboard · BillDetail
│   │   ├── components/           Logo · SplashScreen · ThemeToggle ·
│   │   │                         BillUploader · ModelSelector ·
│   │   │                         ModelComparison · ExtractionResults ·
│   │   │                         EvaluationForm · ZohoExpenseCreator
│   │   ├── services/api.js       Axios instance + every call
│   │   └── constants.js          Provider metadata
│   ├── vercel.json               SPA fallback rewrite
│   └── package.json · vite.config.js · tailwind.config.js
├── dataset/
│   ├── bills/                    receipt images (committed)
│   ├── ground_truth.json         answer key
│   └── README.md                 collection + redaction guide
├── docs/METHODOLOGY.md
├── docker-compose.yml
└── README.md
```

## Troubleshooting

**`ValidationError: This project benchmarks LLMs against each other…`**
Fewer than two API keys in `backend/.env`. Add another, or set
`ALLOW_SINGLE_PROVIDER=true`.

**`failed to connect to the docker API at unix:///Users/…/docker.sock`**
Docker Desktop is installed but not running. Installing it does not start the
daemon — the app has to be open.

```bash
open -a Docker          # then wait for the whale icon in the menu bar to settle
docker info             # succeeds once the daemon is up
```

First launch takes 30-60 seconds. If `docker info` still fails, open Docker
Desktop from Applications and complete any first-run prompt it shows.

**`asyncpg.exceptions.InvalidAuthorizationSpecificationError: role "postgres" does not exist`**
Homebrew's PostgreSQL creates a role named after your macOS user, not
`postgres`. Three ways out, easiest first:

```bash
# 1. Skip native Postgres entirely
docker compose up -d db
#    then set: DATABASE_URL=postgresql+asyncpg://lextract:lextract@localhost:5432/lextract

# 2. Use your own role
psql postgres -c "SELECT current_user;"        # confirm the name
#    then set: DATABASE_URL=postgresql+asyncpg://YOURNAME@localhost:5432/lextract

# 3. Create the role the default DSN expects
psql postgres -c "CREATE ROLE postgres LOGIN SUPERUSER PASSWORD 'postgres';"
```

**`could not translate host name` / `Connection refused` on startup**
Postgres is not running or `lextract` does not exist. `createdb lextract`,
then `alembic upgrade head`. The server still boots so you can reach `/docs`.

**`InvalidPasswordError` from asyncpg**
Fix the credentials in `DATABASE_URL`. Either `postgresql://` or
`postgresql+asyncpg://` is accepted — the app upgrades the driver for you.

**`404 model not found` from a provider**
That model ID was deprecated — it happens constantly. Ask the provider what it
serves right now:

```bash
cd backend
python scripts/list_models.py            # every configured provider
python scripts/list_models.py groq       # just one
```

Paste an ID into the matching `*_MODEL` variable in `.env`. No code change
needed.

**`401 Invalid Authentication` from a provider**
Almost always a valid key aimed at the wrong endpoint, not a bad key. API keys
carry recognisable prefixes, so the client names the mismatch for you:

```
moonshot rejected the API key (401). This key starts with 'sk-or-', which is an
openrouter key, but it was sent to moonshot at https://api.moonshot.ai/v1.
Either set OPENROUTER_API_KEY instead, or point MOONSHOT_BASE_URL at the right
service.
```

| Prefix | Belongs to | Set it as |
|---|---|---|
| `sk-or-v1-…` | OpenRouter | `OPENROUTER_API_KEY` |
| `gsk_…` | Groq | `GROQ_API_KEY` |
| `nvapi-…` | NVIDIA NIM | `NVIDIA_API_KEY` |
| `sk-ant-…` | Anthropic | `ANTHROPIC_API_KEY` |
| `AIza…` | Google AI Studio | `GOOGLE_API_KEY` |

Also check the model ID matches the service: OpenRouter namespaces by lab
(`moonshotai/kimi-k2.6`), native APIs do not (`kimi-k2.6`).

**Groq returns `<think>...` instead of JSON, or `json_validate_failed`**
Groq's vision model (Qwen 3.6 27B) is a *reasoning* model. Its chain of thought
is returned in the content by default and consumes the output budget — on a
dense bill it can exhaust the entire allowance before emitting a single field,
which Groq reports as `json_validate_failed` with an empty generation.

Already handled: the client sends `reasoning_effort=none` and
`reasoning_format=hidden`, and `LLM_MAX_OUTPUT_TOKENS` defaults to 2048. If you
still see it, check `GROQ_REASONING_EFFORT=none` is set in `.env`. Extraction is
transcription, not puzzle-solving — there is nothing to reason about.

**A provider rejects `response_format` or `max_completion_tokens`**
Already handled — the OpenAI-compatible client detects the rejection, drops the
offending parameter and retries once. If you see it in the logs it is
informational, not a failure.

**Published prices have changed and the cost column is wrong**
Set `MODEL_PRICE_OVERRIDES` in `.env`, no code change:
`MODEL_PRICE_OVERRIDES={"qwen/qwen3.6-27b": [0.60, 3.00]}` (USD per 1M tokens,
`[input, output]`).

**OpenAI returns a quota error immediately**
A new key with no credit fails on every call. Add credit under
*Settings → Billing* first.

**"File does not start with JPEG, PNG or WebP magic bytes"**
Uploads are validated by content, not extension. A renamed PDF or HEIC will be
rejected — convert it to JPEG.

**`CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`**
Your Python has no CA bundle. Standard on a python.org install for macOS — it
does not use the system keychain. Any one of these fixes it:

```bash
# 1. Run the installer that shipped with Python (match your version)
open "/Applications/Python 3.12/Install Certificates.command"

# 2. Or install certifi — the scripts pick it up automatically
pip install certifi

# 3. Or use Homebrew's Python, which trusts the system keychain
brew install python@3.12
```

The helper scripts detect this specific failure and print the fix rather than
blaming your authorization code.

**Zoho: `Please enter valid expense account`**
Zoho's expense API takes an account **id**, not a name — the name in the brief's
example payload is not accepted. Pin the id:

1. Zoho Books → *Accountant* → *Chart of Accounts*
2. Click your expense account (must be expense-type; income and asset accounts
   are rejected)
3. Copy the long number from the browser URL
4. `ZOHO_EXPENSE_ACCOUNT_ID=<that number>` in `backend/.env`, restart

This also sidesteps the scope problem below entirely: the name→id lookup is the
only call needing `ZohoBooks.accountants.READ`, so supplying the id removes the
need for that grant.

**Zoho: `You are not authorized to perform this operation` (code 57)**
Your token is valid — it just lacks a permission. Zoho reports every permission
problem with this one code, so find out which:

```bash
curl -s http://localhost:8000/api/zoho/diagnose | python3 -m json.tool
```

It walks the chain one call at a time and names the step that fails. The usual
culprit is `ZohoBooks.accountants.READ` — the chart of accounts is an
*accountant* resource, not a settings one, so it's easy to omit.

**Scopes are baked into the refresh token**, so widening them means generating a
new one; editing `.env` alone does nothing:

```bash
python scripts/get_refresh_token.py --fullaccess
```

Other causes, in order: the authorising Zoho user isn't an Admin of the org, or
`ZOHO_ORGANIZATION_ID` points at a different org than the token covers.

**Zoho: `invalid_code`**
Three causes, in order of likelihood:

1. **Wrong flow for your client type.** A Self Client code must be exchanged
   *without* `redirect_uri`; a Server-based code must include the exact
   registered one. Zoho reports both mismatches as `invalid_code`, which points
   at the wrong thing. Toggle with `--browser`.
2. The code expired — they last minutes and the clock starts when it is
   generated.
3. The code was already used. Each one works exactly once.

**Zoho: no `refresh_token` in the response**
For the browser flow, Zoho only issues one when both `access_type=offline` and
`prompt=consent` are sent — the script always sends both. For a Self Client,
generate a fresh code.

**Zoho: `invalid token` with correct credentials**
Data-centre mismatch. A `.in` account needs `accounts.zoho.in` *and*
`zohoapis.in`; a `.com` account needs both `.com`. Check `ZOHO_ACCOUNTS_DOMAIN`
and `ZOHO_BOOKS_BASE_URL` agree.

**Zoho: "No expense account named …"**
`ZOHO_DEFAULT_EXPENSE_ACCOUNT` must match a name in your chart of accounts
exactly (*Zoho Books → Accountant → Chart of Accounts*).

**Frontend shows "Backend offline"**
Start it: `cd backend && uvicorn app.main:app --reload`. If it is on a
non-default port, set `VITE_API_BASE_URL` in `frontend/.env`.

**CORS errors in the browser console**
The Vite dev proxy should make CORS moot. If you bypassed it with
`VITE_API_BASE_URL`, add that origin to `CORS_ORIGINS` in `backend/.env`.
