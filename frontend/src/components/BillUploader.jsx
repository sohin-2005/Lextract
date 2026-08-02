import { useCallback, useRef, useState } from 'react'
import { AlertCircle, Loader2, UploadCloud } from 'lucide-react'
import { uploadBill } from '../services/api.js'

const ACCEPTED = ['image/jpeg', 'image/png', 'image/webp']
const MAX_BYTES = 10 * 1024 * 1024

/**
 * Drag-and-drop upload zone.
 *
 * Files are checked for type and size in the browser before upload — not as a
 * security measure (the backend re-validates magic bytes, which is the check
 * that actually counts) but so the user gets an instant answer instead of
 * waiting on a round trip for a 40 MB PDF.
 *
 * @param {{ onUploaded?: (bill: object) => void }} props
 */
export default function BillUploader({ onUploaded }) {
  const [dragging, setDragging] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [progress, setProgress] = useState({ done: 0, total: 0 })
  const inputRef = useRef(null)

  const validate = (file) => {
    if (!ACCEPTED.includes(file.type)) {
      return `${file.name}: only JPEG, PNG and WebP are supported.`
    }
    if (file.size > MAX_BYTES) {
      return `${file.name}: ${(file.size / 1024 / 1024).toFixed(1)} MB exceeds the 10 MB limit.`
    }
    return null
  }

  const handleFiles = useCallback(
    async (fileList) => {
      const files = Array.from(fileList || [])
      if (!files.length) return

      setError(null)
      setBusy(true)
      setProgress({ done: 0, total: files.length })

      const failures = []
      for (const [index, file] of files.entries()) {
        const problem = validate(file)
        if (problem) {
          failures.push(problem)
          setProgress({ done: index + 1, total: files.length })
          continue
        }
        try {
          const bill = await uploadBill(file)
          onUploaded?.(bill)
        } catch (err) {
          failures.push(`${file.name}: ${err.message}`)
        }
        setProgress({ done: index + 1, total: files.length })
      }

      setBusy(false)
      setProgress({ done: 0, total: 0 })
      if (failures.length) setError(failures.join(' · '))
      if (inputRef.current) inputRef.current.value = ''
    },
    [onUploaded],
  )

  return (
    <div>
      <div
        role="button"
        tabIndex={0}
        aria-label="Upload bill images"
        onClick={() => !busy && inputRef.current?.click()}
        onKeyDown={(e) => {
          if ((e.key === 'Enter' || e.key === ' ') && !busy) inputRef.current?.click()
        }}
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragging(false)
          if (!busy) handleFiles(e.dataTransfer.files)
        }}
        className={`flex cursor-pointer flex-col items-center justify-center rounded-card border border-dashed
          px-6 py-12 text-center transition-all duration-200
          ${
            dragging
              ? 'border-brand-400 bg-brand-50 shadow-[0_0_0_4px_rgba(4,161,229,0.08)]'
              : 'border-slate-300 bg-slate-50/50 hover:border-brand-300 hover:bg-brand-50/40'
          }
          ${busy ? 'pointer-events-none opacity-60' : ''}`}
      >
        {busy ? (
          <>
            <Loader2 size={26} className="mb-3 animate-spin text-brand-500" />
            <p className="text-sm font-medium text-slate-700">
              Uploading {progress.done} of {progress.total}…
            </p>
            <span className="meter mt-3 w-40">
              <span
                className="meter-fill block"
                style={{ width: `${(progress.done / Math.max(progress.total, 1)) * 100}%` }}
              />
            </span>
          </>
        ) : (
          <>
            <span className="mb-3.5 flex h-11 w-11 items-center justify-center rounded-xl bg-brand-gradient shadow-brand">
              <UploadCloud size={20} className="text-white" />
            </span>
            <p className="text-sm font-medium text-slate-700">
              Drop receipt photos here, or <span className="text-brand-700">browse</span>
            </p>
            <p className="mt-1.5 text-xs text-slate-500">
              JPEG, PNG or WebP · up to 10 MB · multiple files supported
            </p>
          </>
        )}
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPTED.join(',')}
          multiple
          className="hidden"
          onChange={(e) => handleFiles(e.target.files)}
        />
      </div>

      {error && (
        <p className="mt-3 flex items-start gap-2 rounded-xl border border-rose-100 bg-rose-50 p-3 text-sm text-rose-700">
          <AlertCircle size={16} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </p>
      )}
    </div>
  )
}
