import { useEffect, useState } from 'react'
import { AlertCircle, ClipboardCheck, Loader2, Save, Wand2 } from 'lucide-react'
import { submitGroundTruth } from '../services/api.js'

const EMPTY = {
  vendor_name: '',
  bill_number: '',
  date: '',
  amount: '',
  currency: 'INR',
  tax_gst_details: '',
}

/** Blank strings mean "absent" to a human; the API needs a real null. */
function toPayload(form) {
  return {
    vendor_name: form.vendor_name.trim(),
    bill_number: form.bill_number.trim() || null,
    date: form.date || null,
    amount: form.amount,
    currency: form.currency.trim().toUpperCase() || 'INR',
    tax_gst_details: form.tax_gst_details.trim() || null,
  }
}

/**
 * Ground-truth entry form.
 *
 * "Prefill from a model" exists because typing six fields by hand for fifteen
 * bills is where this kind of project quietly dies. Prefilling and *correcting*
 * is far faster than typing from scratch.
 *
 * The anchoring risk is real — a prefilled wrong value is easy to rubber-stamp —
 * so the button is explicit rather than automatic, and the warning below it says
 * so plainly. The bill image sits next to the form for exactly this reason.
 *
 * @param {{
 *   billId: string,
 *   existing: object|null,
 *   suggestions: Array<object>,
 *   onSaved: () => void
 * }} props
 */
export default function EvaluationForm({ billId, existing, suggestions = [], onSaved }) {
  const [form, setForm] = useState(EMPTY)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (!existing) return
    setForm({
      vendor_name: existing.vendor_name ?? '',
      bill_number: existing.bill_number ?? '',
      date: existing.date ?? '',
      amount: existing.amount != null ? String(existing.amount) : '',
      currency: existing.currency ?? 'INR',
      tax_gst_details: existing.tax_gst_details ?? '',
    })
  }, [existing])

  const update = (key) => (event) => {
    setForm((prev) => ({ ...prev, [key]: event.target.value }))
    setSaved(false)
  }

  const prefill = (result) => {
    setForm({
      vendor_name: result.vendor_name ?? '',
      bill_number: result.bill_number ?? '',
      date: result.date ?? '',
      amount: result.amount != null ? String(result.amount) : '',
      currency: result.currency ?? 'INR',
      tax_gst_details: result.tax_gst_details ?? '',
    })
    setSaved(false)
  }

  const handleSubmit = async (event) => {
    event.preventDefault()
    setError(null)

    if (!form.vendor_name.trim()) {
      setError('Vendor name is required. If the shop name is illegible, write what you can read.')
      return
    }
    if (form.amount === '' || Number.isNaN(Number(form.amount))) {
      setError('Amount is required and must be a number (for example 245.50).')
      return
    }

    setSaving(true)
    try {
      await submitGroundTruth(billId, toPayload(form))
      setSaved(true)
      onSaved?.()
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <h3 className="section-title flex items-center gap-2">
          <ClipboardCheck size={16} className="text-teal-600 dark:text-teal-400" />
          {existing ? 'Correct ground truth' : 'Enter ground truth'}
        </h3>
      </div>

      <p className="text-xs leading-relaxed text-muted dark:text-ink-300">
        Read these values off the image yourself. Everything the benchmark reports is measured
        against what you type here, so an error in this form becomes an error in every model's
        score.
      </p>

      {suggestions.length > 0 && (
        <div className="rounded-xl border border-teal-100 dark:border-teal-900/60 bg-teal-soft p-3.5">
          <p className="mb-2.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.07em] text-teal-700 dark:text-teal-300">
            <Wand2 size={13} /> Prefill from a model, then correct it
          </p>
          <div className="flex flex-wrap gap-2">
            {suggestions.map((s) => (
              <button
                key={s.id}
                type="button"
                className="btn-secondary btn-sm !px-2.5 !py-1.5 font-mono !text-[11px]"
                onClick={() => prefill(s)}
              >
                {s.model_name}
              </button>
            ))}
          </div>
          <p className="mt-2.5 text-[11px] leading-relaxed text-amber-700 dark:text-amber-400">
            Prefilling anchors you to that model's answer. Check every field against the image
            before saving, or you will score the models against their own mistakes.
          </p>
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <label className="label" htmlFor="gt-vendor">
            Vendor name <span className="text-rose-500 dark:text-rose-400">*</span>
          </label>
          <input
            id="gt-vendor"
            className="input"
            value={form.vendor_name}
            onChange={update('vendor_name')}
            placeholder="Sharma General Store"
            required
          />
        </div>

        <div>
          <label className="label" htmlFor="gt-number">
            Bill number
          </label>
          <input
            id="gt-number"
            className="input"
            value={form.bill_number}
            onChange={update('bill_number')}
            placeholder="Leave blank if the bill has none"
          />
        </div>

        <div>
          <label className="label" htmlFor="gt-date">
            Date
          </label>
          <input
            id="gt-date"
            type="date"
            className="input"
            value={form.date}
            onChange={update('date')}
          />
        </div>

        <div>
          <label className="label" htmlFor="gt-amount">
            Amount <span className="text-rose-500 dark:text-rose-400">*</span>
          </label>
          <input
            id="gt-amount"
            type="number"
            step="0.01"
            min="0"
            className="input"
            value={form.amount}
            onChange={update('amount')}
            placeholder="245.50"
            required
          />
        </div>

        <div>
          <label className="label" htmlFor="gt-currency">
            Currency
          </label>
          <input
            id="gt-currency"
            className="input"
            value={form.currency}
            onChange={update('currency')}
            maxLength={8}
          />
        </div>

        <div className="sm:col-span-2">
          <label className="label" htmlFor="gt-tax">
            Tax / GST details
          </label>
          <input
            id="gt-tax"
            className="input"
            value={form.tax_gst_details}
            onChange={update('tax_gst_details')}
            placeholder="GSTIN: 07AABCU9603R1ZX — or leave blank"
          />
        </div>
      </div>

      {error && (
        <p className="flex items-start gap-2 rounded-xl border border-rose-100 bg-rose-50 p-3 text-sm text-rose-700 dark:border-rose-900/60 dark:bg-rose-950/40 dark:text-rose-300">
          <AlertCircle size={16} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </p>
      )}

      {saved && !error && (
        <p className="rounded-xl border border-teal-100 bg-teal-50 p-3 text-sm text-teal-700 dark:border-teal-900/60 dark:bg-teal-900/25 dark:text-teal-300">
          Ground truth saved. Run "Evaluate" to score every model against it.
        </p>
      )}

      <button type="submit" className="btn-primary" disabled={saving}>
        {saving ? (
          <>
            <Loader2 size={15} className="animate-spin" /> Saving…
          </>
        ) : (
          <>
            <Save size={15} /> {existing ? 'Update ground truth' : 'Save ground truth'}
          </>
        )}
      </button>
    </form>
  )
}
