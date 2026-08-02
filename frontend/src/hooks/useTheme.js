import { useCallback, useEffect, useState } from 'react'

const STORAGE_KEY = 'lextract-theme'

/** Read the stored preference, falling back to the OS setting. */
function initialTheme() {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    if (stored === 'light' || stored === 'dark') return stored
  } catch {
    // Private browsing can throw on access; the OS preference is a fine default.
  }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

/**
 * Light/dark theme, persisted and synced to `<html class="dark">`.
 *
 * Tailwind is configured with `darkMode: 'class'` rather than `media` so an
 * explicit choice can override the OS — someone on a dark desktop may still
 * want the light view for a screenshot or a projector.
 *
 * @returns {{ theme: 'light'|'dark', toggle: () => void }}
 */
export default function useTheme() {
  const [theme, setTheme] = useState(initialTheme)

  useEffect(() => {
    const root = document.documentElement
    root.classList.toggle('dark', theme === 'dark')
    root.style.colorScheme = theme // native scrollbars and form controls
    try {
      window.localStorage.setItem(STORAGE_KEY, theme)
    } catch {
      // Not being able to persist is not worth breaking the toggle over.
    }
  }, [theme])

  const toggle = useCallback(() => setTheme((t) => (t === 'dark' ? 'light' : 'dark')), [])
  return { theme, toggle }
}
