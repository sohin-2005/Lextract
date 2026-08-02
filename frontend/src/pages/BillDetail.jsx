import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  AlertCircle,
  ArrowLeft,
  Gauge,
  Image as ImageIcon,
  ImageOff,
  Loader2,
  Play,
  RefreshCw,
  Table2,
} from 'lucide-react'
import ModelComparison from '../components/ModelComparison.jsx'
import ExtractionResults from '../components/ExtractionResults.jsx'
import EvaluationForm from '../components/EvaluationForm.jsx'
import ZohoExpenseCreator from '../components/ZohoExpenseCreator.jsx'
import ModelSelector from '../components/ModelSelector.jsx'
import {
  evaluateBill,
  extractBill,
  getBill,
  getHealth,
  getZohoStatus,
  imageUrl,
} from '../services/api.js'

/** Bill detail: image, comparison grid, ground truth, raw responses, Zoho sync. */
export default function BillDetail() {
  const { billId } = useParams()
  const [bill, setBill] = useState(null)
  const [evaluations, setEvaluations] = useState({})
  const [available, setAvailable] = useState([])
  const [models, setModels] = useState({})
  const [selected, setSelected] = useState([])
  const [zohoStatus, setZohoStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [extracting, setExtracting] = useState(false)
  const [evaluating, setEvaluating] = useState(false)
  const [error, setError] = useState(null)
  const [imageBroken, setImageBroken] = useState(false)

  const refresh = useCallback(async () => {
    try {
      setBill(await getBill(billId))
      setError(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [billId])

  /** Re-score against ground truth, ignoring the "nothing to score yet" case. */
  const runEvaluation = useCallback(
    async ({ silent = false } = {}) => {
      if (!silent) setEvaluating(true)
      try {
        const data = await evaluateBill(billId)
        setEvaluations(
          Object.fromEntries(data.evaluations.map((e) => [e.extraction_result_id, e])),
        )
      } catch (err) {
        if (!silent) setError(err.message)
      } finally {
        if (!silent) setEvaluating(false)
      }
    },
    [billId],
  )

  useEffect(() => {
    refresh()
    getHealth()
      .then((h) => {
        setAvailable(h.configured_providers)
        setModels(h.models || {})
        setSelected(h.configured_providers)
      })
      .catch(() => setAvailable([]))
    getZohoStatus()
      .then(setZohoStatus)
      .catch(() => setZohoStatus(null))
  }, [refresh])

  // Auto-score on load when both halves already exist, so returning to a
  // finished bill shows its scores without an extra click.
  useEffect(() => {
    if (bill?.ground_truth && bill?.extraction_results?.some((r) => r.succeeded)) {
      runEvaluation({ silent: true })
    }
  }, [bill?.ground_truth, bill?.extraction_results, runEvaluation])

  const successful = useMemo(
    () => (bill?.extraction_results ?? []).filter((r) => r.succeeded),
    [bill],
  )

  const runExtraction = async () => {
    if (!selected.length) {
      setError('Select at least one model.')
      return
    }
    setExtracting(true)
    setError(null)
    try {
      const result = await extractBill(billId, selected)
      if (result.failed_models.length) {
        setError(`Failed: ${result.failed_models.join(', ')} — see the raw responses below.`)
      }
      await refresh()
    } catch (err) {
      setError(err.message)
    } finally {
      setExtracting(false)
    }
  }

  if (loading) {
    return (
      <div className="space-y-5 py-4">
        <div className="skeleton h-9 w-72" />
        <div className="grid gap-6 lg:grid-cols-[minmax(0,340px)_minmax(0,1fr)]">
          <div className="skeleton h-96 w-full" />
          <div className="skeleton h-96 w-full" />
        </div>
      </div>
    )
  }

  if (!bill) {
    return (
      <div className="card px-6 py-20 text-center">
        <p className="mb-4 text-sm text-rose-600">{error || 'Receipt not found.'}</p>
        <Link to="/" className="btn-secondary">
          <ArrowLeft size={15} /> Back to dashboard
        </Link>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3 animate-fade-up">
        <div>
          <Link
            to="/"
            className="mb-1.5 inline-flex items-center gap-1 text-xs font-medium text-slate-500 transition-colors hover:text-brand-700"
          >
            <ArrowLeft size={13} /> All receipts
          </Link>
          <h1 className="text-xl font-semibold tracking-[-0.015em] text-slate-900">
            {bill.filename}
          </h1>
          <p className="tnum mt-1 text-xs text-slate-500">
            {(bill.size_bytes / 1024).toFixed(0)} KB · {bill.content_type} · uploaded{' '}
            {new Date(bill.uploaded_at).toLocaleString()}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            className="btn-primary"
            onClick={runExtraction}
            disabled={extracting || !selected.length}
          >
            {extracting ? (
              <>
                <Loader2 size={15} className="animate-spin" /> Extracting…
              </>
            ) : (
              <>
                <Play size={15} /> Run extraction
              </>
            )}
          </button>
          <button
            className="btn-secondary"
            onClick={() => runEvaluation()}
            disabled={evaluating || !bill.ground_truth || !successful.length}
            title={
              !bill.ground_truth
                ? 'Submit ground truth first'
                : !successful.length
                  ? 'Run an extraction first'
                  : 'Score every model against ground truth'
            }
          >
            {evaluating ? (
              <>
                <Loader2 size={15} className="animate-spin" /> Scoring…
              </>
            ) : (
              <>
                <Gauge size={15} /> Evaluate
              </>
            )}
          </button>
          <button className="btn-ghost" onClick={refresh} title="Reload">
            <RefreshCw size={15} />
          </button>
        </div>
      </div>

      <div className="card p-5 animate-fade-up" style={{ animationDelay: '60ms' }}>
        <ModelSelector
          available={available}
          models={models}
          selected={selected}
          onChange={setSelected}
        />
      </div>

      {error && (
        <p className="flex items-start gap-2.5 rounded-card border border-rose-100 bg-rose-50 p-3.5 text-sm text-rose-700">
          <AlertCircle size={16} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </p>
      )}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,340px)_minmax(0,1fr)]">
        <div className="space-y-6">
          <section className="card overflow-hidden">
            <div className="flex items-center gap-2 border-b border-slate-100 px-5 py-3">
              <ImageIcon size={15} className="text-brand-500" />
              <span className="section-title">Original receipt</span>
            </div>
            {imageBroken ? (
              <div className="flex flex-col items-center gap-2 p-12 text-slate-400">
                <ImageOff size={26} />
                <p className="text-xs">Image file is no longer on disk.</p>
              </div>
            ) : (
              <a href={imageUrl(bill.id)} target="_blank" rel="noreferrer">
                <img
                  src={imageUrl(bill.id)}
                  alt={`Handwritten bill: ${bill.filename}`}
                  className="max-h-[520px] w-full bg-slate-50 object-contain transition-transform duration-300 hover:scale-[1.015]"
                  onError={() => setImageBroken(true)}
                />
              </a>
            )}
          </section>

          <section className="card p-5">
            <EvaluationForm
              billId={bill.id}
              existing={bill.ground_truth}
              suggestions={successful}
              onSaved={async () => {
                await refresh()
                await runEvaluation({ silent: true })
              }}
            />
          </section>
        </div>

        <div className="space-y-6">
          <section className="card p-6">
            <div className="mb-5">
              <h2 className="section-title flex items-center gap-2">
                <Table2 size={17} className="text-brand-500" />
                Model comparison
              </h2>
              <p className="mt-1 text-xs text-slate-500">
                Rows are fields, columns are models. Read across a row to see which fields are hard
                for every model.
              </p>
            </div>
            <ModelComparison
              results={bill.extraction_results}
              groundTruth={bill.ground_truth}
              evaluations={evaluations}
            />
          </section>

          {successful.length > 0 && (
            <section className="card p-6">
              <h2 className="section-title mb-1">Push to Zoho Books</h2>
              <p className="mb-5 text-xs text-slate-500">
                Creates an expense from the extracted vendor, date and amount. Pick the model you
                trust most for this receipt.
              </p>
              <div className="space-y-4">
                {successful.map((r) => (
                  <div key={r.id} className="rounded-xl border border-slate-200 bg-white/60 p-3.5">
                    <p className="mb-2.5 flex flex-wrap items-center gap-2 text-xs">
                      <span className="font-mono font-semibold text-slate-800">{r.model_name}</span>
                      <span className="tnum text-slate-500">
                        {r.vendor_name || '—'} · {r.currency} {r.amount ?? '—'} · {r.date || '—'}
                      </span>
                    </p>
                    <ZohoExpenseCreator result={r} zohoStatus={zohoStatus} />
                  </div>
                ))}
              </div>
            </section>
          )}

          <section className="card p-6">
            <ExtractionResults results={bill.extraction_results} />
          </section>
        </div>
      </div>
    </div>
  )
}
