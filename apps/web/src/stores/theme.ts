import { defineStore } from 'pinia'
import { ref } from 'vue'

type Theme = 'light' | 'dark'
export const useThemeStore = defineStore('theme', () => {
  const theme = ref<Theme>('light')
  try {
    const saved = localStorage.getItem('workspace-theme')
    if (saved === 'dark' || saved === 'light') theme.value = saved
    else if (window.matchMedia?.('(prefers-color-scheme: dark)').matches) theme.value = 'dark'
  } catch { /* Storage may be unavailable in private or restricted contexts. */ }
  function apply() { document.documentElement.dataset.theme = theme.value }
  function toggle() {
    theme.value = theme.value === 'light' ? 'dark' : 'light'
    apply()
    try { localStorage.setItem('workspace-theme', theme.value) } catch { /* Keep the in-memory preference. */ }
  }
  apply()
  return { theme, toggle }
})
