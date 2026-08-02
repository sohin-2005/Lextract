import { useEffect, useState } from 'react'
import { Link, Navigate, Route, Routes, useLocation, useSearchParams } from 'react-router-dom'
import { Activity, AlertTriangle, CheckCircle2, ExternalLink, LayoutGrid } from 'lucide-react'
import Dashboard from './pages/Dashboard.jsx'
import BillDetail from './pages/BillDetail.jsx'
import SplashScreen from './components/SplashScreen.jsx'
import { getHealth } from './services/api.js'

/** Wordmark. Orbitron is used here and nowhere else. */
function Logo({ compact = false }) {
  return (
    <span className="flex items-center gap-2.5">
      <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-gradient shadow-brand">
        <svg viewBox="0 0 24 24" className="h-[18px] w-[18px]" fill="none" stroke="white" strokeWidth="1.9">
          <path d="M5 3.5h9.5L19 8v12.5H5z" strokeLinejoin="round" />
          <path d="M14 3.5V8h5" strokeLinejoin="round" />
          <path d="M8.4 12.4h7.2M8.4 15.8h4.6" strokeLinecap="round" />
        </svg>
      </span>
      <span className="leading-none">
        <span
          className="block font-display text-[15px] font-bold text-slate-900"
          style={{ letterSpacing: '0.11em' }}
        >
          Lextract
        </span>
        {!compact && (
          <span className="mt-1 block text-[11px] font-medium text-slate-400">
            Receipt extraction &amp; model benchmarking
          </span>
        )}
      </span>
    </span>
  )
}

/**
 * Landing page for the Zoho OAuth redirect.
 *
 * Zoho sends the browser here with `?code=…`. The code is single-use and
 * expires in about a minute, so the page surfaces it immediately and links
 * straight to the exchange endpoint rather than making the user copy it out of
 * the URL bar under time pressure.
 */
function ZohoCallback() {
  const [params] = useSearchParams()
  const code = params.get('code')
  const error = params.get('error')

  return (
    <div className="mx-auto max-w-2xl px-6 py-14">
      <div className="card p-8">
        <h1 className="mb-1 text-xl font-semibold tracking-[-0.01em]">Zoho authorisation</h1>
        <p className="mb-6 text-sm text-slate-500">Step 2 of 2 — exchange the code for a token.</p>

        {error && (
          <p className="rounded-xl bg-rose-50 p-4 text-sm text-rose-700">
            Zoho returned an error: <code className="font-mono">{error}</code>
          </p>
        )}

        {code && (
          <>
            <p className="mb-3 text-sm text-slate-600">
              Authorisation code received. It expires in about 60 seconds — exchange it now.
            </p>
            <code className="mb-5 block break-all rounded-xl border border-slate-200 bg-slate-50 p-3.5 font-mono text-xs text-slate-700">
              {code}
            </code>
            <a
              className="btn-primary"
              href={`/api/zoho/callback?code=${encodeURIComponent(code)}`}
              target="_blank"
              rel="noreferrer"
            >
              Exchange for refresh token <ExternalLink size={15} />
            </a>
            <p className="mt-5 text-sm leading-relaxed text-slate-500">
              Copy <code className="font-mono text-slate-700">refresh_token</code> from the response
              into <code className="font-mono text-slate-700">backend/.env</code> as{' '}
              <code className="font-mono text-slate-700">ZOHO_REFRESH_TOKEN</code>, then restart the
              backend.
            </p>
          </>
        )}

        {!code && !error && (
          <p className="text-sm text-slate-600">
            No <code className="font-mono">code</code> parameter present. Start the flow with{' '}
            <code className="font-mono text-slate-700">python scripts/get_refresh_token.py</code>.
          </p>
        )}

        <Link to="/" className="mt-7 inline-block text-sm font-medium text-brand-700 hover:underline">
          &larr; Back to dashboard
        </Link>
      </div>
    </div>
  )
}

/** Compact backend-health indicator in the header. */
function HealthPill() {
  const [health, setHealth] = useState(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    getHealth()
      .then((data) => !cancelled && setHealth(data))
      .catch(() => !cancelled && setFailed(true))
    return () => {
      cancelled = true
    }
  }, [])

  if (failed) {
    return (
      <span className="badge-danger" title="Start it with: uvicorn app.main:app --reload">
        <AlertTriangle size={12} /> API offline
      </span>
    )
  }
  if (!health) return <span className="badge-neutral">Checking…</span>

  const healthy = health.status === 'ok'
  const count = health.configured_providers.length

  return (
    <div className="flex items-center gap-2">
      <span
        className={healthy ? 'badge-success' : 'badge-warning'}
        title={`Database: ${health.database}`}
      >
        {healthy ? <CheckCircle2 size={12} /> : <AlertTriangle size={12} />}
        {healthy ? 'Connected' : 'DB unavailable'}
      </span>
      <span
        className="badge-brand"
        title={`Models configured: ${health.configured_providers.join(', ') || 'none'}`}
      >
        <Activity size={12} />
        {count} {count === 1 ? 'model' : 'models'}
      </span>
    </div>
  )
}

/** Root component: splash gate, chrome, routing. */
export default function App() {
  const { pathname } = useLocation()
  const [showSplash, setShowSplash] = useState(true)

  return (
    <div className="page-ambience min-h-screen">
      {showSplash && <SplashScreen onDone={() => setShowSplash(false)} />}

      <a
        href="#main"
        className="sr-only-focusable fixed left-4 top-4 z-40 rounded-lg bg-white px-4 py-2 text-sm font-medium shadow-lift"
      >
        Skip to content
      </a>

      <header className="sticky top-0 z-30 border-b border-slate-200/70 bg-white/75 backdrop-blur-xl">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-6 py-3.5">
          <Link to="/" aria-label="Lextract home">
            <Logo />
          </Link>

          <div className="flex items-center gap-3">
            {pathname !== '/' && (
              <Link to="/" className="btn-ghost btn-sm">
                <LayoutGrid size={15} /> Dashboard
              </Link>
            )}
            <a
              className="hidden text-sm font-medium text-slate-500 transition-colors hover:text-brand-700 sm:block"
              href="/docs"
              target="_blank"
              rel="noreferrer"
            >
              API
            </a>
            <span className="hidden h-5 w-px bg-slate-200 sm:block" />
            <HealthPill />
          </div>
        </div>
      </header>

      <main id="main" className="mx-auto max-w-7xl px-6 py-8">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/bills/:billId" element={<BillDetail />} />
          <Route path="/zoho/callback" element={<ZohoCallback />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>

      <footer className="mx-auto max-w-7xl px-6 pb-10 pt-4">
        <div className="divider mb-5" />
        <p className="text-center text-xs text-slate-400">
          <span className="font-display font-semibold tracking-[0.08em] text-slate-500">
            Lextract
          </span>{' '}
          — costs are estimates from published per-token rates
        </p>
      </footer>
    </div>
  )
}
