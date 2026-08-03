import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertCircle,
  BarChart3,
  CheckCircle2,
  Clock,
  Eye,
  FileText,
  Gauge,
  Layers,
  Loader2,
  Play,
  RefreshCw,
  Trash2,
  Wallet,
  XCircle,
} from 'lucide-react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import BillUploader from '../components/BillUploader.jsx'
import ModelSelector from '../components/ModelSelector.jsx'
import {
  deleteBill,
  extractBill,
  getEvaluationReport,
  getHealth,
  listBills,
} from '../services/api.js'

// Status uses the semantic badges, not the accent: teal means "scored well"
// everywhere else in the UI, so reusing it for "uploaded" would blur the signal.
const STATUS = {
  uploaded: { cls: 'badge-neutral', Icon: Clock, label: 'Ready' },
  processing: { cls: 'badge-warning', Icon: Loader2, label: 'Running' },
  completed: { cls: 'badge-success', Icon: CheckCircle2, label: 'Done' },
  failed: { cls: 'badge-danger', Icon: XCircle, label: 'Failed' },
}

function StatusBadge({ status }) {
  const { cls, Icon, label } = STATUS[status] ?? STATUS.uploaded
  return (
    <span className={cls}>
      <Icon size={11} className={status === 'processing' ? 'animate-spin' : ''} />
      {label}
    </span>
  )
}

/**
 * One headline metric.
 *
 * `featured` renders the gradient treatment. Exactly one card per row uses it —
 * the moment two do, neither reads as the primary number.
 */
function StatCard({ icon: Icon, label, value, hint, featured = false }) {
  if (featured) {
    return (
      <div className="card-accent p-5">
        <div className="relative z-10">
          <div className="mb-3 flex items-center justify-between">
            <span className="text-[11px] font-bold uppercase tracking-[0.09em] text-ink-900/70">
              {label}
            </span>
            <Icon size={16} className="text-ink-900/60" />
          </div>
          <p className="tnum text-3xl font-semibold leading-none tracking-[-0.02em]">{value}</p>
          {hint && <p className="mt-2 text-xs font-medium text-ink-900/70">{hint}</p>}
        </div>
      </div>
    )
  }

  return (
    <div className="card card-hover p-5">
      <div className="mb-3 flex items-center justify-between">
        <span className="eyebrow">{label}</span>
        <Icon size={16} className="text-teal-600 dark:text-teal-400" />
      </div>
      <p className="tnum text-3xl font-semibold leading-none tracking-[-0.02em] text-ink-900 dark:text-paper-50">
        {value}
      </p>
      {hint && <p className="mt-2 text-xs text-muted dark:text-ink-300">{hint}</p>}
    </div>
  )
}

// Teal ramp for series colour. The leading model gets the strongest value;
// later entries step back rather than compete for attention.
const BAR_COLOURS = ['#0EA47E', '#1BB58C', '#4FC3A2', '#0B8468', '#8FD9C2']

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-xl border border-paper-300 bg-white/95 px-3 py-2 shadow-lift backdrop-blur dark:border-ink-700 dark:bg-ink-800/95">
      <p className="mb-0.5 font-mono text-[11px] text-muted dark:text-ink-300">{label}</p>
      <p className="tnum text-sm font-semibold text-ink-900 dark:text-paper-50">{payload[0].value}% accuracy</p>
    </div>
  )
}

/** Accuracy / cost leaderboard across every scored bill. */
function Leaderboard({ report, loading, onRefresh }) {
  const chartData = useMemo(
    () =>
      (report?.reports ?? []).map((r) => ({
        name: r.model_name.length > 20 ? `${r.model_name.slice(0, 18)}…` : r.model_name,
        accuracy: Number((r.overall_accuracy * 100).toFixed(1)),
      })),
    [report],
  )

  const rows = report?.reports ?? []

  return (
    <section className="card p-6">
      <div className="mb-5 flex items-center justify-between gap-3">
        <div>
          <h2 className="section-title flex items-center gap-2">
            <BarChart3 size={17} className="text-teal-600 dark:text-teal-400" />
            Model leaderboard
          </h2>
          <p className="mt-1 text-xs text-muted dark:text-ink-300">
            Field-level accuracy against your ground truth, with cost extrapolated to 100 bills.
          </p>
        </div>
        <button className="btn-secondary btn-sm" onClick={onRefresh} disabled={loading}>
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> Refresh
        </button>
      </div>

      {!rows.length ? (
        <div className="rounded-card border border-dashed border-paper-300 dark:border-ink-700 bg-paper-100/70 dark:bg-ink-800/60 px-6 py-12 text-center">
          <Gauge size={24} className="mx-auto mb-3 text-ink-200 dark:text-ink-500" />
          <p className="text-sm font-medium text-ink-600 dark:text-ink-200">No scored bills yet</p>
          <p className="mx-auto mt-1 max-w-sm text-xs leading-relaxed text-muted dark:text-ink-300">
            Upload a receipt, run extraction, enter ground truth, then evaluate. The leaderboard
            fills in from there.
          </p>
        </div>
      ) : (
        <>
          <div className="-ml-2 h-56 w-full text-muted dark:text-ink-400">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} margin={{ top: 8, right: 8, bottom: 4, left: -14 }}>
                <CartesianGrid
                  strokeDasharray="4 4"
                  className="stroke-paper-300 dark:stroke-ink-700"
                  vertical={false}
                />
                <XAxis
                  dataKey="name"
                  tick={{ fontSize: 11, fill: 'currentColor' }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  domain={[0, 100]}
                  unit="%"
                  tick={{ fontSize: 11, fill: 'currentColor' }}
                  axisLine={false}
                  tickLine={false}
                />
                <Tooltip content={<ChartTooltip />} cursor={{ fill: 'rgba(14,164,126,0.08)' }} />
                <Bar dataKey="accuracy" radius={[7, 7, 0, 0]} maxBarSize={64}>
                  {chartData.map((_, i) => (
                    <Cell key={i} fill={BAR_COLOURS[i % BAR_COLOURS.length]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="mt-5 overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="table-head">
                  <th className="pb-2.5 pr-4 font-semibold">Model</th>
                  <th className="pb-2.5 pr-4 font-semibold">Overall</th>
                  <th className="pb-2.5 pr-4 font-semibold">Vendor</th>
                  <th className="pb-2.5 pr-4 font-semibold">Amount</th>
                  <th className="pb-2.5 pr-4 font-semibold">Date</th>
                  <th className="pb-2.5 pr-4 font-semibold">Latency</th>
                  <th className="pb-2.5 pr-4 font-semibold">Cost / 100</th>
                  <th className="pb-2.5 font-semibold">Bills</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-paper-200 dark:divide-ink-800">
                {rows.map((r, index) => (
                  <tr key={r.model_name} className="transition-colors hover:bg-teal-50/50 dark:hover:bg-teal-900/15">
                    <td className="py-3 pr-4">
                      <div className="flex items-center gap-2">
                        <span
                          className="h-2 w-2 shrink-0 rounded-full"
                          style={{ background: BAR_COLOURS[index % BAR_COLOURS.length] }}
                        />
                        <span className="font-mono text-xs text-ink-700 dark:text-paper-200">{r.model_name}</span>
                      </div>
                    </td>
                    <td className="py-3 pr-4">
                      <div className="flex items-center gap-2.5">
                        <span className="tnum w-11 font-semibold text-ink-900 dark:text-paper-50">
                          {(r.overall_accuracy * 100).toFixed(1)}%
                        </span>
                        <span className="meter w-20">
                          <span
                            className="meter-fill block"
                            style={{ width: `${r.overall_accuracy * 100}%` }}
                          />
                        </span>
                      </div>
                    </td>
                    <td className="tnum py-3 pr-4 text-ink-600 dark:text-ink-200">
                      {(r.fields.vendor_name.accuracy * 100).toFixed(0)}%
                    </td>
                    <td className="tnum py-3 pr-4 text-ink-600 dark:text-ink-200">
                      {(r.fields.amount.accuracy * 100).toFixed(0)}%
                    </td>
                    <td className="tnum py-3 pr-4 text-ink-600 dark:text-ink-200">
                      {(r.fields.date.accuracy * 100).toFixed(0)}%
                    </td>
                    <td className="tnum py-3 pr-4 text-ink-600 dark:text-ink-200">
                      {(r.avg_latency_ms / 1000).toFixed(2)}s
                    </td>
                    <td className="tnum py-3 pr-4 text-ink-600 dark:text-ink-200">
                      ${r.cost_per_100_bills_usd.toFixed(2)}
                    </td>
                    <td className="tnum py-3 text-ink-600 dark:text-ink-200">{r.bills_evaluated}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {report.recommendation && (
            <div className="card-tint mt-5 p-4">
              <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.09em] text-teal-700 dark:text-teal-300">Recommendation</p>
              <p className="text-[13px] leading-relaxed text-ink-700 dark:text-paper-200">{report.recommendation}</p>
            </div>
          )}
        </>
      )}
    </section>
  )
}

/** Landing page: metrics, upload, model selection, bill list, leaderboard. */
export default function Dashboard() {
  const [bills, setBills] = useState([])
  const [report, setReport] = useState(null)
  const [available, setAvailable] = useState([])
  const [models, setModels] = useState({})
  const [selected, setSelected] = useState([])
  const [loading, setLoading] = useState(true)
  const [reportLoading, setReportLoading] = useState(false)
  const [running, setRunning] = useState({})
  const [error, setError] = useState(null)

  const refreshBills = useCallback(async () => {
    try {
      setBills(await listBills())
      setError(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  const refreshReport = useCallback(async () => {
    setReportLoading(true)
    try {
      setReport(await getEvaluationReport())
    } catch {
      // An empty report is an expected state, not an error worth surfacing —
      // the panel already renders an explanatory empty state.
    } finally {
      setReportLoading(false)
    }
  }, [])

  useEffect(() => {
    refreshBills()
    refreshReport()
    getHealth()
      .then((h) => {
        setAvailable(h.configured_providers)
        setModels(h.models || {})
        // Pre-select everything available: the point of the tool is comparison,
        // so the default should be "run them all".
        setSelected(h.configured_providers)
      })
      .catch(() => setAvailable([]))
  }, [refreshBills, refreshReport])

  const stats = useMemo(() => {
    const extractions = bills.reduce((sum, b) => sum + (b.extraction_count || 0), 0)
    const withTruth = bills.filter((b) => b.has_ground_truth).length
    const best = report?.reports?.[0]
    return { extractions, withTruth, best }
  }, [bills, report])

  const runExtraction = async (billId) => {
    if (!selected.length) {
      setError('Select at least one model before running extraction.')
      return
    }
    setRunning((prev) => ({ ...prev, [billId]: true }))
    setError(null)
    try {
      const result = await extractBill(billId, selected)
      if (result.failed_models.length) {
        setError(`Some models failed: ${result.failed_models.join(', ')}. Open the bill to see why.`)
      }
      await refreshBills()
    } catch (err) {
      setError(err.message)
    } finally {
      setRunning((prev) => ({ ...prev, [billId]: false }))
    }
  }

  const removeBill = async (billId, filename) => {
    if (!window.confirm(`Delete "${filename}" and all of its extraction results?`)) return
    try {
      await deleteBill(billId)
      await Promise.all([refreshBills(), refreshReport()])
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div className="space-y-7">
      <div className="animate-fade-up">
        <h1 className="text-2xl font-semibold tracking-[-0.02em] text-ink-900 dark:text-paper-50">Dashboard</h1>
        <p className="mt-1.5 text-sm text-muted dark:text-ink-300">
          Extract structured fields from handwritten receipts, then benchmark every model against
          your own ground truth.
        </p>
      </div>

      <section
        className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4 animate-fade-up"
        style={{ animationDelay: '60ms' }}
      >
        <StatCard
          icon={Gauge}
          label="Best accuracy"
          value={stats.best ? `${(stats.best.overall_accuracy * 100).toFixed(1)}%` : '—'}
          hint={stats.best ? stats.best.model_name : 'Evaluate a bill to populate'}
          featured
        />
        <StatCard icon={FileText} label="Receipts" value={bills.length} hint={`${stats.withTruth} with ground truth`} />
        <StatCard icon={Layers} label="Extractions" value={stats.extractions} hint="Across all models" />
        <StatCard
          icon={Wallet}
          label="Cost / 100 bills"
          value={stats.best ? `$${stats.best.cost_per_100_bills_usd.toFixed(2)}` : '—'}
          hint={stats.best ? 'For the leading model' : 'Awaiting evaluation'}
        />
      </section>

      <section className="card p-6 animate-fade-up" style={{ animationDelay: '120ms' }}>
        <h2 className="section-title mb-1">Upload receipts</h2>
        <p className="mb-5 text-xs text-muted dark:text-ink-300">
          Redact phone numbers, personal names and account numbers before uploading — these images
          are sent to third-party model APIs.
        </p>
        <BillUploader onUploaded={() => refreshBills()} />
      </section>

      {error && (
        <p className="flex items-start gap-2.5 rounded-card border border-rose-100 bg-rose-50 p-3.5 text-sm text-rose-700 dark:border-rose-900/60 dark:bg-rose-950/40 dark:text-rose-300">
          <AlertCircle size={16} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </p>
      )}

      <section className="card p-6 animate-fade-up" style={{ animationDelay: '180ms' }}>
        <div className="mb-5 border-b border-paper-200 dark:border-ink-800 pb-5">
          <ModelSelector
            available={available}
            models={models}
            selected={selected}
            onChange={setSelected}
          />
        </div>

        <div className="mb-4 flex items-center justify-between gap-4">
          <h2 className="section-title">
            Receipts <span className="tnum font-normal text-ink-300 dark:text-ink-400">({bills.length})</span>
          </h2>
        </div>

        {loading ? (
          <div className="space-y-2.5 py-2">
            {[0, 1, 2].map((i) => (
              <div key={i} className="skeleton h-12 w-full" />
            ))}
          </div>
        ) : !bills.length ? (
          <div className="rounded-card border border-dashed border-paper-300 dark:border-ink-700 bg-paper-100/70 dark:bg-ink-800/60 px-6 py-12 text-center">
            <FileText size={24} className="mx-auto mb-3 text-ink-200 dark:text-ink-500" />
            <p className="text-sm font-medium text-ink-600 dark:text-ink-200">No receipts yet</p>
            <p className="mt-1 text-xs text-muted dark:text-ink-300">Drop an image above to get started.</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="table-head">
                  <th className="pb-2.5 pr-4 font-semibold">File</th>
                  <th className="pb-2.5 pr-4 font-semibold">Status</th>
                  <th className="pb-2.5 pr-4 font-semibold">Runs</th>
                  <th className="pb-2.5 pr-4 font-semibold">Ground truth</th>
                  <th className="pb-2.5 pr-4 font-semibold">Uploaded</th>
                  <th className="pb-2.5 text-right font-semibold">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-paper-200 dark:divide-ink-800">
                {bills.map((bill) => (
                  <tr key={bill.id} className="group transition-colors hover:bg-teal-50/50 dark:hover:bg-teal-900/15">
                    <td className="py-3 pr-4">
                      <div className="flex items-center gap-2.5">
                        <Link
                          to={`/bills/${bill.id}`}
                          className="font-medium text-ink-800 dark:text-paper-100 transition-colors hover:text-teal-700 dark:hover:text-teal-300"
                        >
                          {bill.filename}
                        </Link>
                        {/* Once a receipt is scored, opening it is the next
                            thing you want to do — an explicit button says so,
                            where a filename that happens to be a link does not. */}
                        {bill.status === 'completed' && (
                          <Link
                            to={`/bills/${bill.id}`}
                            className="btn-secondary btn-sm shrink-0"
                            title={`View ${bill.filename} and its extraction details`}
                            aria-label={`View ${bill.filename} and its extraction details`}
                          >
                            <Eye size={13} /> View
                          </Link>
                        )}
                      </div>
                    </td>
                    <td className="py-3 pr-4">
                      <StatusBadge status={bill.status} />
                    </td>
                    <td className="tnum py-3 pr-4 text-ink-600 dark:text-ink-200">{bill.extraction_count}</td>
                    <td className="py-3 pr-4">
                      {bill.has_ground_truth ? (
                        <span className="badge-success">
                          <CheckCircle2 size={11} /> Set
                        </span>
                      ) : (
                        <span className="text-xs text-ink-300 dark:text-ink-400">Not set</span>
                      )}
                    </td>
                    <td className="py-3 pr-4 text-xs text-muted dark:text-ink-300">
                      {new Date(bill.uploaded_at).toLocaleString()}
                    </td>
                    <td className="py-3 text-right">
                      <div className="flex justify-end gap-1.5">
                        <button
                          className="btn-secondary btn-sm"
                          onClick={() => runExtraction(bill.id)}
                          disabled={running[bill.id] || !selected.length}
                          title={
                            selected.length
                              ? `Run ${selected.join(', ')}`
                              : 'Select at least one model above'
                          }
                        >
                          {running[bill.id] ? (
                            <>
                              <Loader2 size={13} className="animate-spin" /> Running
                            </>
                          ) : (
                            <>
                              <Play size={13} /> Extract
                            </>
                          )}
                        </button>
                        <button
                          className="btn-danger-ghost btn-sm !px-2"
                          onClick={() => removeBill(bill.id, bill.filename)}
                          title="Delete receipt"
                          aria-label={`Delete ${bill.filename}`}
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <div className="animate-fade-up" style={{ animationDelay: '240ms' }}>
        <Leaderboard report={report} loading={reportLoading} onRefresh={refreshReport} />
      </div>
    </div>
  )
}
