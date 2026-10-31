import { describe, expect, it } from 'vitest'
import { z } from 'zod'
import { toTypedSchema } from '../src/services/forms'

// vee-validate 与 zod 4 的适配层（替代只支持 zod 3 的 @vee-validate/zod，T-0044）
describe('toTypedSchema', () => {
  const schema = toTypedSchema(z.object({
    name: z.string().trim().min(1, '请输入名称'),
    token: z.string().min(20, 'Token 至少 20 个字符').or(z.literal('')),
    links: z.array(z.object({ url: z.string().startsWith('https://', '只支持 https') })),
    nested: z.object({ limit: z.number().int().max(20, '最多 20') }),
  }))

  it('returns parsed values without errors', async () => {
    const result = await schema.parse({ name: ' 工作台 ', token: '', links: [], nested: { limit: 5 } })
    expect(result).toEqual({ value: { name: '工作台', token: '', links: [], nested: { limit: 5 } }, errors: [] })
  })

  it('groups messages by vee-validate field paths, including array indexes and nested objects', async () => {
    const result = await schema.parse({ name: ' ', token: '', links: [{ url: 'https://ok' }, { url: 'http://no' }], nested: { limit: 50 } })
    expect(result.value).toBeUndefined()
    expect(result.errors).toEqual([
      { path: 'name', errors: ['请输入名称'] },
      { path: 'links[1].url', errors: ['只支持 https'] },
      { path: 'nested.limit', errors: ['最多 20'] },
    ])
  })

  it('uses the specific reason of each union branch instead of a generic message', async () => {
    const result = await schema.parse({ name: 'x', token: 'short', links: [], nested: { limit: 1 } })
    expect(result.errors).toHaveLength(1)
    expect(result.errors[0]!.path).toBe('token')
    expect(result.errors[0]!.errors).toContain('Token 至少 20 个字符')
  })

  it('casts valid values and keeps invalid ones unchanged', () => {
    expect(schema.cast!({ name: ' a ', token: '', links: [], nested: { limit: 1 } })).toEqual({ name: 'a', token: '', links: [], nested: { limit: 1 } })
    const invalid = { name: '', token: 'x', links: [], nested: { limit: 99 } }
    expect(schema.cast!(invalid)).toBe(invalid)
  })
})
