import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/vue'
import { afterEach, vi } from 'vitest'
// jsdom has no scrolling implementation; routing behavior is tested separately.
window.scrollTo = vi.fn()
afterEach(() => { cleanup(); localStorage.clear(); document.documentElement.removeAttribute('data-theme') })
