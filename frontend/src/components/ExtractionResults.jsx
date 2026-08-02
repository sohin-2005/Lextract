import { useMemo, useState } from 'react'
import { ChevronDown, ChevronRight, Code2, GitCompare } from 'lucide-react'

const FIELDS = ['vendor_name', 'bill_number', 'date', 'amount', 'currency', 'tax_gst_details']

/** Pretty-print JSON, falling back to the raw text when it will not parse. */
function prettify(raw) {
  if (!raw) return '(empty response)'
  try {
    return JSON.stringify(JSON.parse(raw), null, 2)
  } catch {
    return raw
  }
}

/** Normalise for disagreement detection so "INR" and "inr" are not a conflict. */
const norm = (v) => (v === null || v === undefined || v === '' ? '∅' : String(v).trim().toLowerCase())

/**
 * Raw-response viewer plus a model-disagreement summary.
 *
 * The disagreement panel is the useful half. When two models return different
 * values for a field, at least one is wrong — so those fields are exactly where
 * a human should look first, and where ground truth is most worth the effort of
 * writing down. It works without ground truth, which makes it usable on the
 * very first run.
 *
 * @param {{ results: Array<object> }} props
 */
export default function ExtractionResults({ results }) {
  const [expanded, setExpanded] = useState(() => new Set())
  const successful = useMemo(() => results.filter((r) => r.succeeded), [results])

  const disagreements = useMemo(() => {
    if (successful.length < 2) return []
    return FIELDS.map((field) => {
      const values = successful.map((r) => ({ model: r.model_name, value: r[field] }))
      const distinct = new Set(values.map((v) => norm(v.value)))
      return distinct.size > 1 ? { field, values } : null
    }).filter(Boolean)
  }, [successful])

  const toggle = (id) =>
    setExpanded((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })

  if (!results.length) return null

  return (
    <div className="space-y-6">
      {successful.length >= 2 && (
        <div>
          <h3 className="section-title mb-2.5 flex items-center gap-2">
            <GitCompare size={16} className="text-teal-600 dark:text-teal-400" />
            Model disagreements
            <span className="badge-neutral">{disagreements.length}</span>
          </h3>
          {!disagreements.length ? (
            <p className="rounded-xl border border-teal-100 bg-teal-50 p-3.5 text-sm text-teal-700 dark:border-teal-900/60 dark:bg-teal-900/25 dark:text-teal-300">
              All {successful.length} models agree on every field. Strong signal that the extraction
              is correct — though not proof, since models can share a blind spot.
            </p>
          ) : (
            <div className="space-y-2">
              {disagreements.map(({ field, values }) => (
                <div key={field} className="rounded-xl border border-amber-100 bg-amber-50/70 p-3.5 dark:border-amber-900/50 dark:bg-amber-950/30">
                  <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.07em] text-amber-800 dark:text-amber-300">
                    {field.replace(/_/g, ' ')}
                  </p>
                  <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
                    {values.map(({ model, value }) => (
                      <span key={model} className="text-ink-700 dark:text-paper-200">
                        <span className="font-mono text-[11px] text-muted dark:text-ink-300">{model}:</span>{' '}
                        {value === null || value === undefined || value === '' ? (
                          <em className="text-ink-300 dark:text-ink-400">null</em>
                        ) : (
                          String(value)
                        )}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <div>
        <h3 className="section-title mb-2.5 flex items-center gap-2">
          <Code2 size={16} className="text-teal-600 dark:text-teal-400" />
          Raw model responses
        </h3>
        <div className="space-y-2">
          {results.map((r) => {
            const open = expanded.has(r.id)
            return (
              <div key={r.id} className="overflow-hidden rounded-xl border border-paper-300 dark:border-ink-700">
                <button
                  className="flex w-full items-center justify-between gap-3 bg-paper-100/80 dark:bg-ink-800/80 px-3.5 py-2.5 text-left transition-colors hover:bg-teal-50/60 dark:hover:bg-teal-900/20"
                  onClick={() => toggle(r.id)}
                  aria-expanded={open}
                >
                  <span className="flex items-center gap-2">
                    {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    <span className="font-mono text-xs text-ink-700 dark:text-paper-200">{r.model_name}</span>
                    {!r.succeeded && <span className="badge-danger">failed</span>}
                  </span>
                  <span className="text-[11px] text-muted dark:text-ink-300">
                    {new Date(r.created_at).toLocaleString()}
                  </span>
                </button>
                {open && (
                  <pre className="max-h-80 overflow-auto bg-ink-950 p-3.5 font-mono text-[11px] leading-relaxed text-paper-100 dark:bg-black/40">
                    {r.succeeded ? prettify(r.raw_response) : r.error_message}
                  </pre>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
