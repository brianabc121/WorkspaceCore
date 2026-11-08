// 新分组、新页面的 id（T-0161）：小写字母和数字，后端要求 ^[a-z0-9][a-z0-9-]{0,39}$
export function newId(): string {
  const random = typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : `${Date.now().toString(16)}${Math.random().toString(16).slice(2)}`
  return random.replace(/[^a-z0-9]/gi, '').toLowerCase().slice(0, 12)
}
