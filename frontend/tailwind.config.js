/** @type {import('tailwindcss').Config} */
export default {
  // Class-based so the toggle can override the OS preference. See ThemeToggle.
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        /**
         * Brand palette, taken from the logo.
         *
         *   Ink    #14181C  wordmark, mark, body text
         *   Teal   #0EA47E  accent — scores, CTAs, active state
         *   Paper  #F2F0E9  app surface, icon tile
         *   Muted  #6A6E72  tagline, secondary labels
         *
         * The ramps around each are derived, not invented: they exist so a
         * border, a hover tint and a disabled state stay in the same family
         * instead of falling back to a generic grey.
         */
        ink: {
          50: '#F4F5F6',
          100: '#E4E6E8',
          200: '#C7CACD',
          300: '#9BA0A5',
          400: '#6A6E72', // = muted
          500: '#4A4F54',
          600: '#33383D',
          700: '#24292E',
          800: '#1A1F24',
          900: '#14181C', // = ink
          950: '#0F1215',
        },
        teal: {
          50: '#E7F6F1',
          100: '#C6EBDF',
          200: '#8FD9C2',
          300: '#4FC3A2',
          400: '#1BB58C',
          500: '#0EA47E', // = accent
          600: '#0B8468',
          700: '#0A6B55',
          800: '#085743',
          900: '#064334',
        },
        paper: {
          50: '#FBFAF7',
          100: '#F2F0E9', // = paper
          200: '#E8E5DB',
          300: '#DAD6C9',
          400: '#C4BFAE',
        },
        muted: '#6A6E72',
      },
      fontFamily: {
        // The wordmark is a bold grotesque, matching the supplied lockup.
        display: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      borderRadius: { card: '18px', panel: '20px' },
      boxShadow: {
        // Warm-tinted rather than neutral grey: a cool shadow on paper reads
        // as dirt. Two layers — tight contact plus wide diffuse — read as depth.
        card: '0 1px 2px rgba(20, 24, 28, 0.04), 0 8px 24px -12px rgba(20, 24, 28, 0.14)',
        lift: '0 2px 4px rgba(20, 24, 28, 0.05), 0 16px 40px -16px rgba(20, 24, 28, 0.20)',
        accent: '0 2px 6px rgba(14, 164, 126, 0.20), 0 14px 32px -14px rgba(14, 164, 126, 0.45)',
      },
      backgroundImage: {
        'teal-gradient': 'linear-gradient(135deg, #0EA47E 0%, #14B88E 100%)',
        'teal-soft': 'linear-gradient(135deg, #EFF8F4 0%, #E3F3ED 100%)',
      },
      keyframes: {
        'fade-up': {
          '0%': { opacity: '0', transform: 'translateY(8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        'fade-out': { '0%': { opacity: '1' }, '100%': { opacity: '0', visibility: 'hidden' } },
        'logo-in': {
          '0%': { opacity: '0', transform: 'translateY(12px) scale(0.96)' },
          '100%': { opacity: '1', transform: 'translateY(0) scale(1)' },
        },
        caret: { '0%,45%': { opacity: '1' }, '50%,95%': { opacity: '0' }, '100%': { opacity: '1' } },
        ambience: {
          '0%,100%': { transform: 'translate3d(0,0,0) scale(1)', opacity: '0.5' },
          '50%': { transform: 'translate3d(0,-14px,0) scale(1.05)', opacity: '0.7' },
        },
        shimmer: { '100%': { transform: 'translateX(100%)' } },
      },
      animation: {
        'fade-up': 'fade-up 0.45s cubic-bezier(0.16, 1, 0.3, 1) both',
        'fade-out': 'fade-out 0.5s ease forwards',
        'logo-in': 'logo-in 0.85s cubic-bezier(0.16, 1, 0.3, 1) both',
        caret: 'caret 1.05s steps(1) infinite',
        ambience: 'ambience 9s ease-in-out infinite',
        shimmer: 'shimmer 1.6s infinite',
      },
    },
  },
  plugins: [],
}
