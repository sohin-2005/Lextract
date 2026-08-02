import { Moon, Sun } from 'lucide-react'

/**
 * Light/dark switch.
 *
 * Shows the icon of the mode it will switch *to*, which is the convention
 * users already have from every OS settings panel.
 *
 * @param {{ theme: 'light'|'dark', onToggle: () => void }} props
 */
export default function ThemeToggle({ theme, onToggle }) {
  const next = theme === 'dark' ? 'light' : 'dark'
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-label={`Switch to ${next} mode`}
      title={`Switch to ${next} mode`}
      className="flex h-9 w-9 items-center justify-center rounded-xl border border-paper-300
                 bg-white text-muted transition-colors
                 hover:border-teal-300 hover:text-teal-700
                 dark:border-ink-700 dark:bg-ink-800 dark:text-ink-300
                 dark:hover:border-teal-700 dark:hover:text-teal-300"
    >
      {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
    </button>
  )
}
