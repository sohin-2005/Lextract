import { useState } from 'react'
import { AlertCircle, CheckCircle2, ExternalLink, Loader2, Send } from 'lucide-react'
import { createZohoExpense } from '../services/api.js'

/**
 * "Push to Zoho Books" control for a single extraction result.
 *
 * The button is disabled — with the reason shown — rather than hidden when Zoho
 * is unconfigured or the extraction has no amount. A hidden control leaves the
 * user wondering whether the feature exists; a disabled one with a tooltip says
 * exactly what to fix.
 *
 * @param {{ result: object, zohoStatus: object|null }} props
 */
export default function ZohoExpenseCreator({ result, zohoStatus }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [expense, setExpense] = useState(null)
  const [accountName, setAccountName] = useState('')

  const configured = zohoStatus?.configured
  const noAmount = result.amount == null
  const disabled = busy || !configured || noAmount || !result.succeeded

  const blockedReason = !result.succeeded
    ? 'This extraction failed, so there is nothing to sync.'
    : noAmount
      ? 'No amount was extracted. Zoho Books requires a numeric amount.'
      : !configured
        ? `Zoho is not configured. Missing: ${(zohoStatus?.missing ?? []).join(', ') || 'credentials'}.`
        : null

  const push = async () => {
    setBusy(true)
    setError(null)
    try {
      setExpense(await createZohoExpense(result.id, { accountName: accountName.trim() || null }))
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  if (expense?.sync_status === 'synced') {
    return (
      <p className="flex items-center gap-2 rounded-xl border border-teal-100 bg-teal-50 p-3.5 text-sm text-teal-700 dark:border-teal-900/60 dark:bg-teal-900/25 dark:text-teal-300">
        <CheckCircle2 size={16} className="shrink-0" />
        <span>
          Synced to Zoho Books as expense{' '}
          <code className="font-mono text-xs">{expense.zoho_expense_id}</code>
        </span>
        <a
          href="https://books.zoho.in/app#/expenses"
          target="_blank"
          rel="noreferrer"
          className="ml-auto inline-flex items-center gap-1 text-xs hover:underline"
        >
          Open <ExternalLink size={11} />
        </a>
      </p>
    )
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <input
          className="input max-w-[230px] !py-2 !text-xs"
          value={accountName}
          onChange={(e) => setAccountName(e.target.value)}
          placeholder="Expense account (optional)"
          disabled={disabled}
          title="Must match a name in your Zoho chart of accounts. Blank uses ZOHO_DEFAULT_EXPENSE_ACCOUNT."
        />
        <button
          className="btn-primary btn-sm !text-xs"
          onClick={push}
          disabled={disabled}
          title={blockedReason ?? 'Create this expense in Zoho Books'}
        >
          {busy ? (
            <>
              <Loader2 size={13} className="animate-spin" /> Sending…
            </>
          ) : (
            <>
              <Send size={13} /> Push to Zoho
            </>
          )}
        </button>
      </div>

      {blockedReason && <p className="text-[11px] text-ink-300 dark:text-ink-400">{blockedReason}</p>}

      {error && (
        <p className="flex items-start gap-2 rounded-xl border border-rose-100 bg-rose-50 p-3 text-xs text-rose-700 dark:border-rose-900/60 dark:bg-rose-950/40 dark:text-rose-300">
          <AlertCircle size={14} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </p>
      )}
    </div>
  )
}
