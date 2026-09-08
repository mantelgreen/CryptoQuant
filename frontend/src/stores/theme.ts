import { defineStore } from 'pinia'
import { ref } from 'vue'

type Mode = 'light' | 'dark' | 'system'

export const useThemeStore = defineStore(
  'theme',
  () => {
    const mode = ref<Mode>('system')
    const isDark = ref(false)

    function effectiveDark(): boolean {
      if (mode.value === 'system') {
        return window.matchMedia('(prefers-color-scheme: dark)').matches
      }
      return mode.value === 'dark'
    }

    function apply() {
      isDark.value = effectiveDark()
      document.documentElement.classList.toggle('dark', isDark.value)
      document.documentElement.style.colorScheme = isDark.value ? 'dark' : 'light'
      const meta = document.querySelector('meta[name="theme-color"]')
      if (meta) meta.setAttribute('content', isDark.value ? '#0a0d12' : '#f4f5f6')
    }

    function applyInitial() {
      apply()
      window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
        if (mode.value === 'system') apply()
      })
    }

    function setMode(m: Mode) {
      mode.value = m
      apply()
    }

    function toggle() {
      setMode(isDark.value ? 'light' : 'dark')
    }

    return { mode, isDark, applyInitial, setMode, toggle }
  },
  {
    persist: {
      key: 'cq:theme',
      paths: ['mode'],
    },
  },
)
