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
      <p className="text-sm text-slate-400">
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
            <Cpu size={13} className="text-brand-500" />
            Models to run
          </span>
          <div className="flex items-center gap-2.5 text-xs">
            <span className="tnum text-slate-400">
              {selected.length}/{providers.length} selected
            </span>
            <button
              type="button"
              className="font-medium text-brand-700 transition-colors hover:text-brand-500"
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
                    ? 'border-brand-300 bg-brand-50 shadow-[0_1px_2px_rgba(4,161,229,0.10)]'
                    : 'border-slate-200 bg-white hover:border-brand-200 hover:bg-brand-50/40'
                }`}
            >
              <span
                className={`flex h-[17px] w-[17px] shrink-0 items-center justify-center rounded-[5px]
                  border transition-all duration-150
                  ${
                    isSelected
                      ? 'border-brand-500 bg-brand-gradient text-white'
                      : 'border-slate-300 bg-white group-hover:border-brand-300'
                  }`}
                aria-hidden="true"
              >
                {isSelected && <Check size={11} strokeWidth={3.5} />}
              </span>

              <span className="min-w-0">
                <span
                  className={`block text-[13px] font-semibold leading-tight ${
                    isSelected ? 'text-brand-800' : 'text-slate-700'
                  }`}
                >
                  {provider.label}
                </span>
                <span
                  className={`block truncate font-mono text-[10px] leading-tight ${
                    isSelected ? 'text-brand-600' : 'text-slate-400'
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
