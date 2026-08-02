/**
 * Axios instance and every Lextract backend call in one place.
 *
 * Centralising the HTTP layer means components never touch axios directly, so
 * error shaping, base-URL resolution and timeouts are defined once instead of
 * being reinvented (slightly differently) in each component.
 */
import axios from 'axios'

/**
 * Base URL for the API.
 *
 * Empty in development, where Vite's dev proxy forwards /api to :8000 and
 * requests are same-origin. In a production build there is no proxy — the
 * proxy is a dev-server feature — so VITE_API_BASE_URL must be set or every
 * request lands on the frontend's own origin and 404s.
 *
 * A trailing slash or an accidental `/api` suffix would produce `//api` or
 * `/api/api`, so both are trimmed rather than left to fail confusingly.
 */
function resolveBaseUrl() {
  const raw = (import.meta.env.VITE_API_BASE_URL || '').trim()
  if (!raw) return ''
  return raw.replace(/\/+$/, '').replace(/\/api$/, '')
}

export const API_BASE_URL = resolveBaseUrl()

/**
 * True when the app was built for production but has no backend URL.
 *
 * This is the single most common deployment mistake with this stack, and its
 * symptom — "failed to fetch" — points at the network rather than at the
 * missing variable. Detecting it lets the error say what to actually do.
 */
export const MISSING_API_BASE_URL = import.meta.env.PROD && !API_BASE_URL

if (MISSING_API_BASE_URL) {
  // eslint-disable-next-line no-console
  console.error(
    '[Lextract] VITE_API_BASE_URL is not set. This build will call /api on its ' +
      'own origin, where no backend exists. Set it to your backend URL (e.g. ' +
      'https://your-backend.onrender.com) in your host\'s environment variables ' +
      'and redeploy — Vite inlines it at build time.',
  )
}

const client = axios.create({
  baseURL: API_BASE_URL,
  // Vision extraction across three models can legitimately take a while; the
  // default 0 (no timeout) would hang forever on a dead backend, and 30s would
  // abort perfectly healthy runs.
  timeout: 180_000,
  headers: { 'Content-Type': 'application/json' },
})

/**
 * Turn any axios failure into a plain Error carrying the backend's own message.
 *
 * FastAPI puts the useful text in `detail`, which may be a string or a list of
 * validation objects. Without this, components would surface the useless
 * "Request failed with status code 422".
 */
function toFriendlyError(error) {
  if (error.code === 'ECONNABORTED') {
    return new Error('Request timed out. The models may still be running — refresh in a moment.')
  }
  if (!error.response) {
    if (MISSING_API_BASE_URL) {
      return new Error(
        'This deployment has no backend URL configured. Set VITE_API_BASE_URL ' +
          'to your backend origin (e.g. https://your-backend.onrender.com) in ' +
          'your hosting provider and redeploy — Vite inlines it at build time.',
      )
    }
    if (API_BASE_URL) {
      return new Error(
        `Cannot reach the backend at ${API_BASE_URL}. If it is hosted on a free ` +
          'tier it may be cold-starting, which can take up to a minute — retry. ' +
          "Otherwise check the backend's CORS_ORIGINS includes this site's origin.",
      )
    }
    return new Error(
      'Cannot reach the backend. Is it running on http://localhost:8000? ' +
        'Start it with: uvicorn app.main:app --reload',
    )
  }

  const { status, data } = error.response
  const detail = data?.detail

  if (typeof detail === 'string') return new Error(detail)
  if (Array.isArray(detail)) {
    const messages = detail
      .map((d) => `${(d.loc || []).slice(1).join('.') || 'field'}: ${d.msg}`)
      .join('; ')
    return new Error(messages || `Validation failed (HTTP ${status}).`)
  }
  if (data?.context?.errors) {
    const messages = data.context.errors
      .map((d) => `${(d.loc || []).slice(1).join('.') || 'field'}: ${d.msg}`)
      .join('; ')
    return new Error(messages || data.detail || `HTTP ${status}`)
  }
  return new Error(data?.detail || `Request failed with HTTP ${status}.`)
}

client.interceptors.response.use(
  (response) => response,
  (error) => Promise.reject(toFriendlyError(error)),
)

/** Absolute URL for a bill image, usable directly as an <img src>. */
export const imageUrl = (billId) => `${API_BASE_URL}/api/bills/${billId}/image`

/* ------------------------------------------------------------------ bills */

/** Upload one bill image. @param {File} file @returns {Promise<Object>} */
export async function uploadBill(file) {
  const form = new FormData()
  form.append('file', file)
  const { data } = await client.post('/api/bills/upload', form, {
    // Let the browser set the multipart boundary; a hardcoded header breaks it.
    headers: { 'Content-Type': undefined },
  })
  return data
}

/** List every uploaded bill, newest first. */
export async function listBills() {
  const { data } = await client.get('/api/bills')
  return data
}

/** Full detail for one bill, including its extractions and ground truth. */
export async function getBill(billId) {
  const { data } = await client.get(`/api/bills/${billId}`)
  return data
}

/** Delete a bill and everything derived from it. */
export async function deleteBill(billId) {
  await client.delete(`/api/bills/${billId}`)
}

/* ------------------------------------------------------------- extraction */

/**
 * Run the selected models over a bill.
 * @param {string} billId
 * @param {string[]} models e.g. ['gemini', 'claude']
 */
export async function extractBill(billId, models) {
  const { data } = await client.post(`/api/extract/${billId}`, { models })
  return data
}

/** Every extraction attempt recorded for a bill. */
export async function getResults(billId) {
  const { data } = await client.get(`/api/extract/${billId}/results`)
  return data
}

/* ------------------------------------------------------------- evaluation */

/** Submit (or correct) the human answer key for a bill. */
export async function submitGroundTruth(billId, payload) {
  const { data } = await client.post(`/api/ground-truth/${billId}`, payload)
  return data
}

/** Score every extraction for a bill against its ground truth. */
export async function evaluateBill(billId) {
  const { data } = await client.post(`/api/evaluate/${billId}`)
  return data
}

/** Cross-bill leaderboard: accuracy, latency and cost per model. */
export async function getEvaluationReport() {
  const { data } = await client.get('/api/evaluation/report')
  return data
}

/* ------------------------------------------------------------------- zoho */

/** Whether the backend holds usable Zoho credentials. */
export async function getZohoStatus() {
  const { data } = await client.get('/api/zoho/status')
  return data
}

/** Step 1 of the OAuth flow: the consent URL. */
export async function getZohoAuthUrl() {
  const { data } = await client.get('/api/zoho/auth-url')
  return data
}

/** Push one extraction into Zoho Books as an expense. */
export async function createZohoExpense(extractionResultId, options = {}) {
  const { data } = await client.post('/api/zoho/expenses', {
    extraction_result_id: extractionResultId,
    account_name: options.accountName ?? null,
    description: options.description ?? null,
  })
  return data
}

/* ----------------------------------------------------------------- system */

/** Health probe: DB reachability plus which providers are configured. */
export async function getHealth() {
  const { data } = await client.get('/api/health')
  return data
}

export default client
