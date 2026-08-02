import { Check, Cpu } from 'lucide-react'
import { visibleProviders } from '../constants.js'

/**
 * Model picker used by both the dashboard and the bill detail page.
 *
 * Rendered as toggle chips rather than bare checkboxes. Each chip carries the
 * provider name and the exact model ID underneath, because "Groq" is not the
 * thing being benchmarked — `qwen/qwen3.6-27b` is, and that is what has to
 * appear in the results table. Surfacing it at the point of selection means you
 * never have to go read `.env` to find out what you just ran.
 *
 * Selection state is owned by the parent, so the dashboard and the detail page
 * each keep their own.
 *
 * @param {{
 *   available: string[],
 *   models?: Record<string, string>,
 *   selected: string[],
 *   onChange: (next: string[]) => void,
 *   compact?: boolean,
 * }} props
 */
export default function ModelSelector({
  available,
  models = {},
  selected,
  onChange,
  compact = false,
}) {
  const providers = visibleProviders(available)

  if (!providers.length) {
    return (
      <p className="text-sm text-ink-300 dark:text-ink-400">
        No models available — add an API key to <code className="font-mono">backend/.env</code> and
        restart.
      </p>
    )
  }

  const toggle = (id) =>
    onChange(selected.includes(id) ? selected.filter((p) => p !== id) : [...selected, id])

  const allSelected = selected.length === providers.length

  return (
    <div className={compact ? '' : 'space-y-2.5'}>
      {!compact && (
        <div className="flex items-center justify-between gap-3">
          <span className="eyebrow flex items-center gap-1.5">
            <Cpu size={13} className="text-teal-600 dark:text-teal-400" />
            Models to run
          </span>
          <div className="flex items-center gap-2.5 text-xs">
            <span className="tnum text-ink-300 dark:text-ink-400">
              {selected.length}/{providers.length} selected
            </span>
            <button
              type="button"
              className="font-medium text-teal-700 dark:text-teal-300 transition-colors hover:text-teal-500"
              onClick={() => onChange(allSelected ? [] : providers.map((p) => p.id))}
            >
              {allSelected ? 'Clear' : 'Select all'}
            </button>
          </div>
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {providers.map((provider) => {
          const isSelected = selected.includes(provider.id)
          const modelId = models[provider.id]
          return (
            <button
              key={provider.id}
              type="button"
              role="checkbox"
              aria-checked={isSelected}
              onClick={() => toggle(provider.id)}
              title={modelId ? `${provider.vendor} · ${modelId}` : provider.vendor}
              className={`group flex items-center gap-2.5 rounded-xl border px-3.5 py-2.5 text-left
                transition-all duration-150 active:scale-[0.985]
                ${
                  isSelected
                    ? 'border-teal-300 dark:border-teal-700 bg-teal-50 dark:bg-teal-900/25 shadow-[0_1px_2px_rgba(4,161,229,0.10)]'
                    : 'border-paper-300 dark:border-ink-700 bg-white hover:border-teal-200 dark:hover:border-teal-800 hover:bg-teal-50/50 dark:hover:bg-teal-900/15'
                }`}
            >
              <span
                className={`flex h-[17px] w-[17px] shrink-0 items-center justify-center rounded-[5px]
                  border transition-all duration-150
                  ${
                    isSelected
                      ? 'border-teal-500 bg-teal-gradient text-white'
                      : 'border-paper-400 dark:border-ink-600 bg-white group-hover:border-teal-300'
                  }`}
                aria-hidden="true"
              >
                {isSelected && <Check size={11} strokeWidth={3.5} />}
              </span>

              <span className="min-w-0">
                <span
                  className={`block text-[13px] font-semibold leading-tight ${
                    isSelected ? 'text-teal-800 dark:text-teal-200' : 'text-ink-700 dark:text-paper-200'
                  }`}
                >
                  {provider.label}
                </span>
                <span
                  className={`block truncate font-mono text-[10px] leading-tight ${
                    isSelected ? 'text-teal-700 dark:text-teal-300' : 'text-ink-300 dark:text-ink-400'
                  }`}
                >
                  {modelId || provider.vendor}
                </span>
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
