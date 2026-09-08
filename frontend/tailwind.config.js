/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      fontFamily: {
        sans: ['var(--font-sans)'],
        display: ['var(--font-display)'],
        mono: ['var(--font-mono)'],
      },
      colors: {
        bg: 'var(--color-bg)',
        surface: 'var(--color-surface)',
        'surface-2': 'var(--color-surface-2)',
        inset: 'var(--color-inset)',
        border: 'var(--color-border)',
        strong: 'var(--color-strong)',
        fg: 'var(--color-fg)',
        muted: 'var(--color-muted)',
        subtle: 'var(--color-subtle)',
        primary: 'var(--color-primary)',
        'primary-ink': 'var(--color-primary-ink)',
        up: 'var(--color-up)',
        down: 'var(--color-down)',
        warn: 'var(--color-warn)',
        danger: 'var(--color-danger)',
        ai: 'var(--color-ai)',
        'ai-ink': 'var(--color-ai-ink)',
      },
      boxShadow: {
        panel: 'var(--shadow-panel)',
        pop: 'var(--shadow-pop)',
      },
      borderRadius: {
        panel: 'var(--radius-panel)',
      },
    },
  },
  plugins: [],
}
