import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/vue'
import { afterEach, vi } from 'vitest'
// jsdom has no scrolling implementation; routing behavior is tested separately.
window.scrollTo = vi.fn()
// GridStack observes actual element sizes in browsers; jsdom has no layout engine.
globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} }
// CodeMirror 量文字位置要用 Range 的几何接口；jsdom 没有布局，给空结果即可（T-0099）
if (!Range.prototype.getClientRects) Range.prototype.getClientRects = () => Object.assign([], { item: () => null }) as unknown as DOMRectList
if (!Range.prototype.getBoundingClientRect) Range.prototype.getBoundingClientRect = () => new DOMRect()
afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear(); sessionStorage.clear(); document.documentElement.removeAttribute('data-theme') })
