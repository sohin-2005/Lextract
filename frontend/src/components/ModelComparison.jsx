import { useMemo } from 'react'
import { AlertTriangle, Clock, DollarSign, History, Target } from 'lucide-react'

const FIELDS = [
  { key: 'vendor_name', label: 'Vendor name' },
  { key: 'bill_number', label: 'Bill number' },
  { key: 'date', label: 'Date' },
  { key: 'amount', label: 'Amount' },
  { key: 'currency', label: 'Currency' },
  { key: 'tax_gst_details', label: 'Tax / GST' },
]

/** Score badge colours. Green ≥0.9, amber ≥0.7, red below — mirrors the rubric.
 *  Deliberately not the brand ramp: correctness is a different axis from brand,
 *  and colouring a failing field blue would bury it. */
function scoreStyle(score) {
  if (score >= 0.9) return 'bg-emerald-50 text-emerald-700'
  if (score >= 0.7) return 'bg-amber-50 text-amber-700'
  return 'bg-rose-50 text-rose-700'
}

function formatValue(value) {
  if (value === null || value === undefined || value === '') return null
  return String(value)
}

/**
 * Collapse repeated runs to the newest attempt per model.
 *
 * Results are an append-only audit log, so extracting the same bill twice
 * leaves two rows for the same model — and two identical columns in this grid.
 * The comparison shows current state; the full history stays visible in the raw
 * responses panel below, which is what the log is for.
 *
 * @param {Array<object>} results
 * @returns {{ rows: Array<object>, runCounts: Record<string, number> }}
 */
function latestPerModel(results) {
  const runCounts = {}
  const newest = new Map()
  for (const result of results) {
    runCounts[result.model_name] = (runCounts[result.model_name] || 0) + 1
    const current = newest.get(result.model_name)
    if (!current || new Date(result.created_at) >= new Date(current.created_at)) {
      newest.set(result.model_name, result)
    }
  }
  const rows = [...newest.values()].sort(
    (a, b) => new Date(a.created_at) - new Date(b.created_at),
  )
  return { rows, runCounts }
}

/**
 * Field-by-field comparison grid: rows are fields, columns are models.
 *
 * The layout is deliberately transposed relative to the raw API shape. Reading
 * *down* a column tells you how one model did; reading *across* a row tells you
 * which field is hard for every model — which is the more actionable question,
 * and the reason ground truth gets its own pinned column on the left.
 *
 * @param {{
 *   results: Array<object>,
 *   groundTruth: object|null,
 *   evaluations: Record<string, object>
 * }} props
 */
export default function ModelComparison({ results, groundTruth, evaluations = {} }) {
  const { rows: successful, runCounts } = useMemo(
    () => latestPerModel(results.filter((r) => r.succeeded)),
    [results],
  )
  // Only the newest failure per model, for the same reason.
  const failed = useMemo(
    () => latestPerModel(results.filter((r) => !r.succeeded)).rows,
    [results],
  )
  const rerunCount = useMemo(
    () => Object.values(runCounts).filter((n) => n > 1).length,
    [runCounts],
  )

  if (!results.length) {
    return (
      <p className="py-10 text-center text-sm text-slate-500">
        No extractions yet. Run one from the dashboard or with the button above.
      </p>
    )
  }

  return (
    <div className="space-y-4">
      {rerunCount > 0 && (
        <p className="flex items-start gap-2 rounded-xl border border-slate-100 bg-slate-50 p-3 text-xs text-slate-500">
          <History size={14} className="mt-px shrink-0 text-brand-500" />
          <span>
            Showing the newest run per model. Earlier runs are kept in the raw responses below
            and are excluded from scoring, so a re-run never counts twice.
          </span>
        </p>
      )}

      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-sm">
          <thead>
            <tr className="table-head">
              <th className="w-36 pb-2.5 pr-4 font-semibold">Field</th>
              {groundTruth && (
                <th className="pb-2.5 pr-4 font-semibold text-brand-700">Ground truth</th>
              )}
              {successful.map((r) => (
                <th key={r.id} className="pb-2.5 pr-4 font-semibold">
                  <span className="block font-mono text-[11px] normal-case text-slate-800">
                    {r.model_name}
                  </span>
                  <span className="flex items-center gap-1.5 text-[10px] font-normal normal-case text-slate-400">
                    {r.provider}
                    {runCounts[r.model_name] > 1 && (
                      <span
                        className="rounded bg-brand-50 px-1.5 py-px font-medium text-brand-700"
                        title={`${runCounts[r.model_name]} runs recorded; showing the newest. Earlier runs are in the raw responses below.`}
                      >
                        run {runCounts[r.model_name]}
                      </span>
                    )}
                  </span>
                </th>
              ))}
            </tr>
          </thead>

          <tbody className="divide-y divide-slate-100">
            {FIELDS.map(({ key, label }) => (
              <tr key={key} className="align-top">
                <td className="py-3 pr-4 text-xs font-semibold text-slate-500">{label}</td>

                {groundTruth && (
                  <td className="bg-brand-50/40 py-3 pr-4">
                    <span className="font-medium text-slate-800">
                      {formatValue(groundTruth[key]) ?? (
                        <span className="text-xs italic text-slate-400">null</span>
                      )}
                    </span>
                  </td>
                )}

                {successful.map((r) => {
                  const score = evaluations[r.id]?.fields?.[key]
                  const value = formatValue(r[key])
                  return (
                    <td key={r.id} className="py-3 pr-4">
                      <div className="flex flex-col gap-1">
                        <span className={value ? 'text-slate-800' : 'text-xs italic text-slate-400'}>
                          {value ?? 'null'}
                        </span>
                        {score && (
                          <span
                            className={`badge w-fit ${scoreStyle(score.score)}`}
                            title={score.notes || score.match_type}
                          >
                            {score.match_type} · {score.score.toFixed(2)}
                          </span>
                        )}
                      </div>
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>

          <tfoot className="border-t border-slate-200">
            <tr>
              <td className="py-3 pr-4 text-xs font-semibold text-slate-600">
                <span className="flex items-center gap-1">
                  <Target size={12} /> Accuracy
                </span>
              </td>
              {groundTruth && <td className="py-3 pr-4 text-xs text-slate-400">—</td>}
              {successful.map((r) => {
                const overall = evaluations[r.id]?.overall_accuracy
                return (
                  <td key={r.id} className="py-3 pr-4 font-semibold">
                    {overall === undefined ? (
                      <span className="text-xs font-normal text-slate-400">not evaluated</span>
                    ) : (
                      <span className="flex flex-col gap-1.5">
                        <span
                          className={`tnum ${overall >= 0.9 ? 'text-emerald-600' : 'text-slate-900'}`}
                        >
                          {(overall * 100).toFixed(1)}%
                        </span>
                        <span className="meter w-20">
                          <span className="meter-fill block" style={{ width: `${overall * 100}%` }} />
                        </span>
                      </span>
                    )}
                  </td>
                )
              })}
            </tr>
            <tr>
              <td className="py-3 pr-4 text-xs font-semibold text-slate-600">
                <span className="flex items-center gap-1">
                  <Clock size={12} /> Latency
                </span>
              </td>
              {groundTruth && <td className="py-3 pr-4 text-xs text-slate-400">—</td>}
              {successful.map((r) => (
                <td key={r.id} className="py-3 pr-4 text-slate-700">
                  {(r.latency_ms / 1000).toFixed(2)}s
                </td>
              ))}
            </tr>
            <tr>
              <td className="py-3 pr-4 text-xs font-semibold text-slate-600">
                <span className="flex items-center gap-1">
                  <DollarSign size={12} /> Cost
                </span>
              </td>
              {groundTruth && <td className="py-3 pr-4 text-xs text-slate-400">—</td>}
              {successful.map((r) => (
                <td key={r.id} className="py-3 pr-4">
                  <span className="tnum text-slate-700">${r.cost_usd.toFixed(5)}</span>
                  <span className="block text-[11px] text-slate-400">
                    ${(r.cost_usd * 100).toFixed(2)} / 100 bills
                  </span>
                  <span
                    className="block text-[10px] text-slate-400"
                    title={
                      r.token_source === 'provider'
                        ? 'Token counts reported by the provider — exact.'
                        : 'Provider returned no usage data; tokens estimated from length.'
                    }
                  >
                    {r.input_tokens}+{r.output_tokens} tok · {r.token_source}
                  </span>
                </td>
              ))}
            </tr>
          </tfoot>
        </table>
      </div>

      {failed.map((r) => (
        <p
          key={r.id}
          className="flex items-start gap-2 rounded-xl border border-rose-100 bg-rose-50 p-3 text-sm text-rose-700"
        >
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          <span>
            <strong className="font-mono text-xs">{r.model_name}</strong> failed: {r.error_message}
          </span>
        </p>
      ))}
    </div>
  )
}
