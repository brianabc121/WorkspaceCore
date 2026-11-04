import { describe, expect, it } from 'vitest'
import type { Schema } from '../src/services/api'
import { addDays, bars, clipOf, compact, duration, overdueBy, parseDay, projectColors, rangeOf, shift, startOfWeek, ticks, tokenText } from '../src/features/timeline/model'

const at = (h: number, m = 0) => new Date(2026, 10, 4, h, m).getTime()
const iso = (h: number, m = 0) => new Date(at(h, m)).toISOString()
const run = (over: Partial<Schema['TaskRunOut']> = {}): Schema['TaskRunOut'] => ({
  id: 'r1', task_id: 't1', executor_kind: 'instance', user_id: null, instance_id: 'i1', executor_name: '甲', started_at: iso(10), ended_at: null, end_reason: null,
  started_by: null, ended_by: null, tokens: { input: 1000, output: 500, cache_read: 0, cache_write: 0, total: 1500 }, tokens_shared: false, usage_reported: true, waits: [], series: [], ...over,
})
const task = (over: Partial<Schema['TimelineTask']> = {}): Schema['TimelineTask'] => ({
  id: 't1', project_id: 'p1', project_title: '发布', title: '打包', done: false, done_at: null, due_at: null, planned_start_at: null, started_at: iso(10),
  assignee_user_id: null, assignee_instance_id: 'i1', running: true, ...over,
})

describe('timeline ranges', () => {
  it('parses and steps days, weeks and hours', () => {
    expect(parseDay('2026-11-04')?.getDate()).toBe(4)
    expect(parseDay('2026-02-30')).toBeNull()
    expect(parseDay('x')).toBeNull()
    const day = new Date(2026, 10, 4, 15)
    const d = rangeOf('day', day)
    expect([d.start.getHours(), d.end.getTime() - d.start.getTime()]).toEqual([0, 24 * 3600_000])
    const w = rangeOf('week', day)
    expect(w.start.getDay()).toBe(1) // 周一
    expect(startOfWeek(new Date(2026, 10, 8)).getDate()).toBe(2) // 周日属于前一周
    const h = rangeOf('hour', day, new Date(2026, 10, 4, 14, 32))
    expect([new Date(h.start).getHours(), new Date(h.start).getMinutes()]).toEqual([11, 30])
    expect(h.end.getTime() - h.start.getTime()).toBe(6 * 3600_000)
    const other = rangeOf('hour', day, new Date(2026, 10, 9, 9))
    expect(new Date(other.start).getHours()).toBe(9) // 不是今天：以中午为中心
    expect(shift('week', day, 1).getDate()).toBe(11)
    expect(shift('day', day, -1).getDate()).toBe(3)
    expect(addDays(day, 2).getDate()).toBe(6)
  })

  it('builds ticks for each scale', () => {
    const d = rangeOf('day', new Date(2026, 10, 4))
    const dayTicks = ticks('day', d.start, d.end)
    expect(dayTicks).toHaveLength(24)
    expect(dayTicks.filter(t => t.label)).toHaveLength(12)
    expect(dayTicks[12].left).toBeCloseTo(50)
    const w = rangeOf('week', new Date(2026, 10, 4))
    expect(ticks('week', w.start, w.end)).toHaveLength(7)
    const h = rangeOf('hour', new Date(2026, 10, 4), new Date(2026, 10, 4, 14, 32))
    expect(ticks('hour', h.start, h.end)).toHaveLength(24)
  })
})

describe('clips', () => {
  const start = at(0), end = at(24), now = at(14)

  it('places running, done and planned segments', () => {
    const running = clipOf({ kind: 'run', task_id: 't1', track: 'instance:i1', start: iso(12), end: null, run: run({ started_at: iso(12) }) }, task(), start, end, now)!
    expect(running.state).toBe('running')
    expect(running.left).toBeCloseTo(50)
    expect(running.width).toBeCloseTo((2 / 24) * 100)
    const done = clipOf({ kind: 'run', task_id: 't1', track: 'x', start: iso(9), end: iso(10), run: run({ ended_at: iso(10), end_reason: 'done' }) }, task(), start, end, now)!
    expect(done.state).toBe('done')
    const planned = clipOf({ kind: 'planned', task_id: 't2', track: 'unassigned', start: iso(15), end: iso(17), run: null }, undefined, start, end, now)!
    expect(planned.state).toBe('planned')
    expect(planned.key).toBe('plan:t2')
    const deadline = clipOf({ kind: 'planned', task_id: 't3', track: 'unassigned', start: null, end: iso(18), run: null }, undefined, start, end, now)!
    expect(deadline.state).toBe('deadline')
    expect(clipOf({ kind: 'planned', task_id: 't4', track: 'u', start: null, end: null, run: null }, undefined, start, end, now)).toBeNull()
    // 范围外的不画；跨范围边界的裁到边界
    expect(clipOf({ kind: 'run', task_id: 't1', track: 'x', start: iso(1), end: iso(2), run: run() }, task(), at(3), end, now)).toBeNull()
    const cut = clipOf({ kind: 'run', task_id: 't1', track: 'x', start: new Date(at(0) - 3600_000).toISOString(), end: iso(1), run: run({ end_reason: 'paused', ended_at: iso(1) }) }, task(), start, end, now)!
    expect([cut.left, cut.state]).toEqual([0, 'paused'])
  })

  it('marks overdue, the ghost until the deadline and approval waits', () => {
    const late = clipOf({ kind: 'run', task_id: 't1', track: 'x', start: iso(12), end: null, run: run({ waits: [{ execution_id: 'e', tool: 'complete_task', start: iso(13), end: iso(13, 30) }] }) }, task({ due_at: iso(13) }), start, end, now)!
    expect(late.overdue?.left).toBeCloseTo(50)
    expect(late.ghost).toBeNull()
    expect(late.waits).toHaveLength(1)
    expect(late.waits[0].left).toBeCloseTo(50)
    expect(late.waits[0].width).toBeCloseTo(25)
    const early = clipOf({ kind: 'run', task_id: 't1', track: 'x', start: iso(12), end: null, run: run() }, task({ due_at: iso(16) }), start, end, now)!
    expect(early.overdue).toBeNull()
    expect(early.ghost?.width).toBeCloseTo((2 / 24) * 100)
    expect(overdueBy(task({ due_at: iso(13) }), now)).toBe(3600_000)
    expect(overdueBy(task({ due_at: iso(13), done: true }), now)).toBeNull()
  })
})

describe('texts and colors', () => {
  it('formats tokens, durations and project colors', () => {
    expect([compact(950), compact(1530), compact(182_340), compact(2_500_000)]).toEqual(['950', '1.5k', '182k', '2.5M'])
    expect(tokenText(run())).toBe('1.5k token')
    expect(tokenText(run({ tokens_shared: true }))).toBe('≈1.5k token')
    expect(tokenText(run({ usage_reported: false, tokens: null }))).toBe('token 未上报')
    expect(tokenText(run({ executor_kind: 'user', tokens: null }))).toBe('人，不计 token')
    expect([duration(5 * 60_000), duration(72 * 60_000), duration(26 * 3600_000)]).toEqual(['5 分', '1 小时 12 分', '1 天 2 小时'])
    const colors = projectColors(['a', 'b', 'c', 'd', 'e', 'f', 'g'])
    expect([colors.get('a'), colors.get('f'), colors.get('g')]).toEqual([1, 6, 0])
  })

  it('scales usage bars to the busiest bucket', () => {
    const usage: Schema['TimelineUsage'] = { scope: 'workspace', bucket_minutes: 60, reported: true, total: { input: 0, output: 0, cache_read: 0, cache_write: 0, total: 300 },
      buckets: [{ start: iso(9), tokens: { input: 100, output: 0, cache_read: 0, cache_write: 0, total: 100 } }, { start: iso(10), tokens: { input: 200, output: 0, cache_read: 0, cache_write: 0, total: 200 } }] }
    const out = bars(usage, at(0), at(24))
    expect(out.map(b => b.height)).toEqual([50, 100])
    expect(out[0].width).toBeCloseTo(100 / 24)
  })
})
