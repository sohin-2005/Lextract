/**
 * Lextract mark and lockup.
 *
 * The mark is a receipt: an "L" whose foot tears off into a perforated edge,
 * with three ruled lines to its right — the top one accented to read as the
 * field that matters. It is drawn once here and reused everywhere, so the app
 * icon, header and splash can never drift apart.
 *
 * Colours follow the theme rather than being baked in:
 *   • the L body uses `currentColor`, so it is ink on paper and paper on ink
 *   • the top rule is always teal, in both modes
 *   • the lower rules use a neutral that lifts in dark mode for contrast
 */

/** Perforated tear-off edge: five teeth across the foot. */
function tearPath(x, width, top, depth, teeth = 5) {
  const step = width / teeth
  let d = `M${x} ${top}`
  for (let i = 0; i < teeth; i += 1) {
    d += ` L${x + step * (i + 0.5)} ${top + depth} L${x + step * (i + 1)} ${top}`
  }
  return `${d} Z`
}

/**
 * The icon mark on its own.
 *
 * @param {{ className?: string, title?: string }} props
 */
export function LogoMark({ className = 'h-8 w-8', title }) {
  return (
    <svg
      viewBox="0 0 120 118"
      className={className}
      role={title ? 'img' : 'presentation'}
      aria-hidden={title ? undefined : 'true'}
      fill="none"
    >
      {title && <title>{title}</title>}

      {/* L body — vertical stroke with a rounded cap, plus the foot. */}
      <g fill="currentColor">
        <rect x="16" y="10" width="22" height="88" rx="11" />
        <rect x="16" y="84" width="88" height="14" />
        <path d={tearPath(16, 88, 98, 15)} />
      </g>

      {/* Ruled lines. The first is the accent. */}
      <rect x="50" y="14" width="54" height="17" rx="8.5" className="fill-teal-500" />
      <rect
        x="50"
        y="39"
        width="38"
        height="17"
        rx="8.5"
        className="fill-ink-300 dark:fill-ink-500"
      />
      <rect
        x="50"
        y="62"
        width="48"
        height="17"
        rx="8.5"
        className="fill-ink-300 dark:fill-ink-500"
      />
    </svg>
  )
}

/**
 * Mark plus wordmark, optionally with the tagline.
 *
 * @param {{ tagline?: boolean, className?: string }} props
 */
export default function Logo({ tagline = false, className = '' }) {
  return (
    <span className={`flex items-center gap-3 ${className}`}>
      <LogoMark className="h-9 w-9 shrink-0 text-ink-900 dark:text-paper-50" />
      <span className="leading-none">
        <span className="block font-display text-[19px] font-extrabold tracking-[-0.03em] text-ink-900 dark:text-paper-50">
          Lextract
        </span>
        {tagline && (
          <span className="mt-1 block text-[11.5px] font-medium tracking-[-0.005em] text-muted dark:text-ink-300">
            Extract. Compare. Evaluate.
          </span>
        )}
      </span>
    </span>
  )
}
