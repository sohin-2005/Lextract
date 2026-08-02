/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        /**
         * Brand ramp built around #04A1E5.
         *
         * 500 is the accent proper. 600/700 exist because 500 on white is
         * ~2.9:1 — fine for large text, borders and fills, but it fails WCAG AA
         * for body copy. Small text on light backgrounds uses 700.
         */
        brand: {
          50: '#EFF9FE',
          100: '#D5F0FC',
          200: '#A9E1F9',
          300: '#6FCDF3',
          400: '#2FB5EC',
          500: '#04A1E5',
          600: '#0384BE',
          700: '#056A98',
          800: '#0A587C',
          900: '#0D4A67',
        },
      },
      fontFamily: {
        // Orbitron is reserved for the wordmark. Using it for UI would read as
        // sci-fi, which is the opposite of the tone this tool needs.
        display: ['Orbitron', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      borderRadius: {
        card: '18px',
        panel: '20px',
      },
      boxShadow: {
        // Two-layer shadows: a tight contact shadow plus a wide diffuse one.
        // A single large blur reads as a drop shadow; two layers read as depth.
        card: '0 1px 2px rgba(15, 39, 56, 0.04), 0 8px 24px -12px rgba(15, 39, 56, 0.12)',
        lift: '0 2px 4px rgba(15, 39, 56, 0.05), 0 16px 40px -16px rgba(15, 39, 56, 0.18)',
        brand: '0 2px 6px rgba(4, 161, 229, 0.20), 0 14px 32px -14px rgba(4, 161, 229, 0.45)',
        inset: 'inset 0 1px 0 rgba(255, 255, 255, 0.6)',
      },
      backgroundImage: {
        /**
         * Two brand gradients, split by whether white text sits on top.
         *
         * `brand-gradient` is the specified #05A9E8 -> #08B4EC. White on it is
         * only 2.4:1, which fails WCAG AA, so it is used exclusively for
         * decoration: icon tiles, meter fills, chart bars, checkbox fills.
         *
         * `brand-deep` is the same hue pulled down the ramp until white clears
         * 4.9:1. Anything carrying white text — primary buttons, the featured
         * stat card — uses it. Same colour family, legible on a projector and
         * for low-vision users.
         */
        'brand-gradient': 'linear-gradient(135deg, #05A9E8 0%, #08B4EC 100%)',
        'brand-deep': 'linear-gradient(135deg, #05658F 0%, #0378AC 100%)',
        'brand-soft': 'linear-gradient(135deg, #F2FAFE 0%, #E7F5FD 100%)',
      },
      keyframes: {
        'fade-up': {
          '0%': { opacity: '0', transform: 'translateY(8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        'fade-out': {
          '0%': { opacity: '1' },
          '100%': { opacity: '0', visibility: 'hidden' },
        },
        'logo-in': {
          '0%': { opacity: '0', transform: 'translateY(14px) scale(0.97)', letterSpacing: '0.32em' },
          '100%': { opacity: '1', transform: 'translateY(0) scale(1)', letterSpacing: '0.14em' },
        },
        'caret': {
          '0%, 45%': { opacity: '1' },
          '50%, 95%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        'ambience': {
          '0%, 100%': { transform: 'translate3d(0,0,0) scale(1)', opacity: '0.55' },
          '50%': { transform: 'translate3d(0,-16px,0) scale(1.06)', opacity: '0.75' },
        },
        'shimmer': {
          '100%': { transform: 'translateX(100%)' },
        },
      },
      animation: {
        'fade-up': 'fade-up 0.45s cubic-bezier(0.16, 1, 0.3, 1) both',
        'fade-out': 'fade-out 0.5s ease forwards',
        'logo-in': 'logo-in 0.9s cubic-bezier(0.16, 1, 0.3, 1) both',
        caret: 'caret 1.05s steps(1) infinite',
        ambience: 'ambience 9s ease-in-out infinite',
        shimmer: 'shimmer 1.6s infinite',
      },
    },
  },
  plugins: [],
}
