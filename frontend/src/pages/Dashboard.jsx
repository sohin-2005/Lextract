import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertCircle,
  BarChart3,
  CheckCircle2,
  Clock,
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
      <div className="card-brand p-5">
        <div className="relative z-10">
          <div className="mb-3 flex items-center justify-between">
            <span className="text-[11px] font-semibold uppercase tracking-[0.09em] text-white/90">
              {label}
            </span>
            <Icon size={16} className="text-white/90" />
          </div>
          <p className="tnum text-3xl font-semibold leading-none tracking-[-0.02em]">{value}</p>
          {hint && <p className="mt-2 text-xs text-white/85">{hint}</p>}
        </div>
      </div>
    )
  }

  return (
    <div className="card card-hover p-5">
      <div className="mb-3 flex items-center justify-between">
        <span className="eyebrow">{label}</span>
        <Icon size={16} className="text-brand-500" />
      </div>
      <p className="tnum text-3xl font-semibold leading-none tracking-[-0.02em] text-slate-900">
        {value}
      </p>
      {hint && <p className="mt-2 text-xs text-slate-500">{hint}</p>}
    </div>
  )
}

const BAR_COLOURS = ['#04A1E5', '#08B4EC', '#6FCDF3', '#0384BE', '#A9E1F9']

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-xl border border-slate-200 bg-white/95 px-3 py-2 shadow-lift backdrop-blur">
      <p className="mb-0.5 font-mono text-[11px] text-slate-500">{label}</p>
      <p className="tnum text-sm font-semibold text-slate-900">{payload[0].value}% accuracy</p>
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
            <BarChart3 size={17} className="text-brand-500" />
            Model leaderboard
          </h2>
          <p className="mt-1 text-xs text-slate-500">
            Field-level accuracy against your ground truth, with cost extrapolated to 100 bills.
          </p>
        </div>
        <button className="btn-secondary btn-sm" onClick={onRefresh} disabled={loading}>
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> Refresh
        </button>
      </div>

      {!rows.length ? (
        <div className="rounded-card border border-dashed border-slate-200 bg-slate-50/60 px-6 py-12 text-center">
          <Gauge size={24} className="mx-auto mb-3 text-slate-300" />
          <p className="text-sm font-medium text-slate-600">No scored bills yet</p>
          <p className="mx-auto mt-1 max-w-sm text-xs leading-relaxed text-slate-500">
            Upload a receipt, run extraction, enter ground truth, then evaluate. The leaderboard
            fills in from there.
          </p>
        </div>
      ) : (
        <>
          <div className="-ml-2 h-56 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} margin={{ top: 8, right: 8, bottom: 4, left: -14 }}>
                <CartesianGrid strokeDasharray="4 4" stroke="#E2E8F0" vertical={false} />
                <XAxis
                  dataKey="name"
                  tick={{ fontSize: 11, fill: '#64748B' }}
                  axisLine={{ stroke: '#E2E8F0' }}
                  tickLine={false}
                />
                <YAxis
                  domain={[0, 100]}
                  unit="%"
                  tick={{ fontSize: 11, fill: '#64748B' }}
                  axisLine={false}
                  tickLine={false}
                />
                <Tooltip content={<ChartTooltip />} cursor={{ fill: 'rgba(4,161,229,0.06)' }} />
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
              <tbody className="divide-y divide-slate-100">
                {rows.map((r, index) => (
                  <tr key={r.model_name} className="transition-colors hover:bg-brand-50/40">
                    <td className="py-3 pr-4">
                      <div className="flex items-center gap-2">
                        <span
                          className="h-2 w-2 shrink-0 rounded-full"
                          style={{ background: BAR_COLOURS[index % BAR_COLOURS.length] }}
                        />
                        <span className="font-mono text-xs text-slate-700">{r.model_name}</span>
                      </div>
                    </td>
                    <td className="py-3 pr-4">
                      <div className="flex items-center gap-2.5">
                        <span className="tnum w-11 font-semibold text-slate-900">
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
                    <td className="tnum py-3 pr-4 text-slate-600">
                      {(r.fields.vendor_name.accuracy * 100).toFixed(0)}%
                    </td>
                    <td className="tnum py-3 pr-4 text-slate-600">
                      {(r.fields.amount.accuracy * 100).toFixed(0)}%
                    </td>
                    <td className="tnum py-3 pr-4 text-slate-600">
                      {(r.fields.date.accuracy * 100).toFixed(0)}%
                    </td>
                    <td className="tnum py-3 pr-4 text-slate-600">
                      {(r.avg_latency_ms / 1000).toFixed(2)}s
                    </td>
                    <td className="tnum py-3 pr-4 text-slate-600">
                      ${r.cost_per_100_bills_usd.toFixed(2)}
                    </td>
                    <td className="tnum py-3 text-slate-600">{r.bills_evaluated}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {report.recommendation && (
            <div className="card-tint mt-5 p-4">
              <p className="eyebrow mb-1.5 text-brand-700">Recommendation</p>
              <p className="text-[13px] leading-relaxed text-slate-700">{report.recommendation}</p>
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
        <h1 className="text-2xl font-semibold tracking-[-0.02em] text-slate-900">Dashboard</h1>
        <p className="mt-1.5 text-sm text-slate-500">
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
        <p className="mb-5 text-xs text-slate-500">
          Redact phone numbers, personal names and account numbers before uploading — these images
          are sent to third-party model APIs.
        </p>
        <BillUploader onUploaded={() => refreshBills()} />
      </section>

      {error && (
        <p className="flex items-start gap-2.5 rounded-card border border-rose-100 bg-rose-50 p-3.5 text-sm text-rose-700">
          <AlertCircle size={16} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </p>
      )}

      <section className="card p-6 animate-fade-up" style={{ animationDelay: '180ms' }}>
        <div className="mb-5 border-b border-slate-100 pb-5">
          <ModelSelector
            available={available}
            models={models}
            selected={selected}
            onChange={setSelected}
          />
        </div>

        <div className="mb-4 flex items-center justify-between gap-4">
          <h2 className="section-title">
            Receipts <span className="tnum font-normal text-slate-400">({bills.length})</span>
          </h2>
        </div>

        {loading ? (
          <div className="space-y-2.5 py-2">
            {[0, 1, 2].map((i) => (
              <div key={i} className="skeleton h-12 w-full" />
            ))}
          </div>
        ) : !bills.length ? (
          <div className="rounded-card border border-dashed border-slate-200 bg-slate-50/60 px-6 py-12 text-center">
            <FileText size={24} className="mx-auto mb-3 text-slate-300" />
            <p className="text-sm font-medium text-slate-600">No receipts yet</p>
            <p className="mt-1 text-xs text-slate-500">Drop an image above to get started.</p>
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
              <tbody className="divide-y divide-slate-100">
                {bills.map((bill) => (
                  <tr key={bill.id} className="group transition-colors hover:bg-brand-50/40">
                    <td className="py-3 pr-4">
                      <Link
                        to={`/bills/${bill.id}`}
                        className="font-medium text-slate-800 transition-colors hover:text-brand-700"
                      >
                        {bill.filename}
                      </Link>
                    </td>
                    <td className="py-3 pr-4">
                      <StatusBadge status={bill.status} />
                    </td>
                    <td className="tnum py-3 pr-4 text-slate-600">{bill.extraction_count}</td>
                    <td className="py-3 pr-4">
                      {bill.has_ground_truth ? (
                        <span className="badge-success">
                          <CheckCircle2 size={11} /> Set
                        </span>
                      ) : (
                        <span className="text-xs text-slate-400">Not set</span>
                      )}
                    </td>
                    <td className="py-3 pr-4 text-xs text-slate-500">
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
