/**
 * Provider metadata shared by the dashboard and the bill detail page.
 *
 * Single source of truth: both pages render the same picker, and a duplicated
 * list is a list that drifts. Which of these actually appear is decided by the
 * backend (`ENABLED_PROVIDERS` + which keys are set), never here — so enabling
 * a provider is a `.env` edit, not a frontend change.
 */
export const PROVIDERS = [
  { id: 'gemini', label: 'Gemini', vendor: 'Google', tier: 'free' },
  { id: 'groq', label: 'Groq', vendor: 'Qwen on LPUs', tier: 'free' },
  { id: 'moonshot', label: 'Kimi', vendor: 'Moonshot AI', tier: 'trial' },
  { id: 'openrouter', label: 'OpenRouter', vendor: 'multi-lab gateway', tier: 'free' },
  { id: 'nvidia', label: 'NVIDIA', vendor: 'Llama Vision on NIM', tier: 'free' },
  { id: 'sambanova', label: 'SambaNova', vendor: 'Llama 4 on RDUs', tier: 'free' },
  { id: 'mistral', label: 'Mistral', vendor: 'Pixtral', tier: 'free' },
  { id: 'claude', label: 'Claude', vendor: 'Anthropic', tier: 'paid' },
  { id: 'openai', label: 'GPT', vendor: 'OpenAI', tier: 'paid' },
]

/**
 * Providers to render, given what the backend reports as available.
 *
 * Only available providers are shown. A permanently greyed-out control for a
 * provider you have no key for is noise: it invites a click that cannot work
 * and makes the row harder to scan.
 *
 * Any available slug missing from PROVIDERS still renders, labelled by its
 * slug, so a newly added backend provider is never silently invisible.
 *
 * @param {string[]} available Provider slugs the backend will accept.
 * @returns {Array<{id: string, label: string, vendor: string, tier: string}>}
 */
export function visibleProviders(available) {
  if (!available?.length) return []
  const known = PROVIDERS.filter((p) => available.includes(p.id))
  const extras = available
    .filter((id) => !PROVIDERS.some((p) => p.id === id))
    .map((id) => ({ id, label: id, vendor: 'custom provider', tier: '' }))
  return [...known, ...extras]
}
