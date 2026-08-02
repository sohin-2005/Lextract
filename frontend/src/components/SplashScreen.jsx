import { useEffect, useRef, useState } from 'react'

const TAGLINE = 'Extract. Compare. Evaluate. Powered by AI.'

const LOGO_SETTLE_MS = 620 // wordmark finishes its entrance
const TYPE_SPEED_MS = 34 // per character
const HOLD_AFTER_TYPING_MS = 620 // beat before dismissing
const FADE_MS = 480

/** True when the OS asks for reduced motion. */
function prefersReducedMotion() {
  return (
    typeof window !== 'undefined' &&
    window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  )
}

/**
 * Brand splash shown once per page load.
 *
 * Deliberately short: an intro screen is a toll booth on the way to the work,
 * so it earns roughly two and a half seconds and no more. It is skippable by
 * click or any key, dismisses immediately under `prefers-reduced-motion`, and
 * is hidden from assistive tech — a screen-reader user gains nothing from a
 * typewriter effect and should land straight on the dashboard.
 *
 * @param {{ onDone: () => void }} props
 */
export default function SplashScreen({ onDone }) {
  const [typed, setTyped] = useState('')
  const [phase, setPhase] = useState('intro') // intro → typing → leaving
  const dismissed = useRef(false)
  const timers = useRef([])

  // One dismissal path for every trigger, so a click mid-animation cannot
  // race the automatic timeout and fire onDone twice.
  const dismiss = useRef(() => {})
  dismiss.current = () => {
    if (dismissed.current) return
    dismissed.current = true
    timers.current.forEach(clearTimeout)
    setPhase('leaving')
    setTimeout(onDone, FADE_MS)
  }

  useEffect(() => {
    if (prefersReducedMotion()) {
      setTyped(TAGLINE)
      const t = setTimeout(() => dismiss.current(), 700)
      timers.current.push(t)
      return () => clearTimeout(t)
    }

    let typingInterval
    const startTyping = setTimeout(() => {
      setPhase('typing')
      let index = 0
      typingInterval = setInterval(() => {
        index += 1
        setTyped(TAGLINE.slice(0, index))
        if (index >= TAGLINE.length) {
          clearInterval(typingInterval)
          const hold = setTimeout(() => dismiss.current(), HOLD_AFTER_TYPING_MS)
          timers.current.push(hold)
        }
      }, TYPE_SPEED_MS)
    }, LOGO_SETTLE_MS)

    timers.current.push(startTyping)
    return () => {
      clearTimeout(startTyping)
      clearInterval(typingInterval)
      timers.current.forEach(clearTimeout)
    }
  }, [])

  useEffect(() => {
    const skip = () => dismiss.current()
    window.addEventListener('keydown', skip)
    return () => window.removeEventListener('keydown', skip)
  }, [])

  return (
    <div
      aria-hidden="true"
      onClick={() => dismiss.current()}
      className={`fixed inset-0 z-50 flex cursor-pointer flex-col items-center justify-center
        overflow-hidden bg-white ${phase === 'leaving' ? 'animate-fade-out' : ''}`}
    >
      {/* Ambient wash — two soft brand blooms plus a faint grid, so the field
          reads as "light and engineered" rather than plain white. */}
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          backgroundImage:
            'radial-gradient(44rem 26rem at 50% 34%, rgba(4,161,229,0.14), transparent 62%),' +
            'radial-gradient(30rem 20rem at 82% 78%, rgba(8,180,236,0.10), transparent 60%)',
        }}
      />
      <div
        className="pointer-events-none absolute inset-0 animate-ambience opacity-60"
        style={{
          backgroundImage:
            'radial-gradient(26rem 16rem at 16% 74%, rgba(4,161,229,0.10), transparent 64%)',
        }}
      />
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.55]"
        style={{
          backgroundImage:
            'linear-gradient(rgba(15,39,56,0.035) 1px, transparent 1px),' +
            'linear-gradient(90deg, rgba(15,39,56,0.035) 1px, transparent 1px)',
          backgroundSize: '64px 64px',
          maskImage: 'radial-gradient(52rem 32rem at 50% 42%, #000 0%, transparent 72%)',
          WebkitMaskImage:
            'radial-gradient(52rem 32rem at 50% 42%, #000 0%, transparent 72%)',
        }}
      />

      <div className="relative flex flex-col items-center px-6">
        <div
          className="mb-7 flex h-14 w-14 animate-logo-in items-center justify-center
                     rounded-2xl bg-brand-gradient shadow-brand"
        >
          <svg viewBox="0 0 24 24" className="h-7 w-7" fill="none" stroke="white" strokeWidth="1.8">
            <path d="M5 3.5h9.5L19 8v12.5H5z" strokeLinejoin="round" />
            <path d="M14 3.5V8h5" strokeLinejoin="round" />
            <path d="M8.4 12.4h7.2M8.4 15.8h4.6" strokeLinecap="round" />
          </svg>
        </div>

        <h1
          className="animate-logo-in font-display text-[2.6rem] font-bold leading-none
                     text-slate-900 sm:text-[3.4rem]"
          style={{ letterSpacing: '0.14em', animationDelay: '80ms' }}
        >
          Lextract
        </h1>

        <div
          className="mt-6 h-6 animate-fade-up"
          style={{ animationDelay: '460ms' }}
        >
          <p className="font-mono text-[13px] tracking-[0.02em] text-slate-500 sm:text-sm">
            {typed}
            <span
              className={`ml-0.5 inline-block h-[1.05em] w-[2px] translate-y-[0.18em]
                bg-brand-500 ${phase === 'typing' ? 'animate-caret' : 'opacity-0'}`}
            />
          </p>
        </div>

        <div
          className="mt-12 h-[3px] w-44 overflow-hidden rounded-full bg-slate-100 animate-fade-up"
          style={{ animationDelay: '620ms' }}
        >
          <div
            className="h-full rounded-full bg-brand-gradient transition-[width] ease-out"
            style={{
              width: `${Math.round((typed.length / TAGLINE.length) * 100)}%`,
              transitionDuration: `${TYPE_SPEED_MS * 2}ms`,
            }}
          />
        </div>
      </div>
    </div>
  )
}
