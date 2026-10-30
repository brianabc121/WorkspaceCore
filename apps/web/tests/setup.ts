import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/vue'
import { afterEach, vi } from 'vitest'
// jsdom has no scrolling implementation; routing behavior is tested separately.
window.scrollTo = vi.fn()
// GridStack observes actual element sizes in browsers; jsdom has no layout engine.
globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} }
afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear(); document.documentElement.removeAttribute('data-theme') })
