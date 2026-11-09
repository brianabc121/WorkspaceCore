// 日历周视图布局（T-0169，四页改造规范 §6）：固定时区的纯函数用例，以及两种来源都把分页加载完。
import { fireEvent, screen, within } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'
import { dayFrames, entriesByDay, layoutWeekEvents, type CalendarEvent, type DueTask, type WeekLayout } from '../src/features/calendar/weekLayout'
import { rangeTitle, timedRange } from '../src/features/calendar/labels'
import { addDays } from '../src/features/calendar/time'
import { calendarEvent } from './sprint4-fixtures'
import { sync, task } from './sprint3-fixtures'
import { fail, mockApi, openApp } from './helpers'

const timed = (id: string, start_at: string, end_at: string, timezone = 'Asia/Shanghai'): CalendarEvent => ({ ...calendarEvent, id, title: id, start_at, end_at, timezone })
const allDay = (id: string, start_date: string, end_date: string, timezone: string, start_at: string, end_at: string): CalendarEvent => ({ ...calendarEvent, id, title: id, all_day: true, start_date, end_date, timezone, start_at, end_at })
const due = (id: string, due_at: string, done = false): DueTask => ({ ...task, id, title: id, due_at, done })
const week = (start: string) => ({ start, end: addDays(start, 7) })
const segments = (layout: WeekLayout, id: string) => layout.days.flatMap((day) => day.segments.filter((segment) => segment.event.id === id).map((segment) => ({ date: day.date, top: segment.top, bottom: segment.bottom, startsBefore: segment.startsBefore, endsAfter: segment.endsAfter, column: segment.column, columns: segment.columns })))
const lane = (layout: WeekLayout, key: string) => layout.lane.find((item) => item.key === key)

describe('周视图布局：时区与日期边界', () => {
  it('cuts days at Shanghai midnight and keeps a midnight-ending event on its own day', () => {
    const events = [timed('after-midnight', '2026-11-09T16:00:00Z', '2026-11-09T17:00:00Z'), timed('until-midnight', '2026-11-09T15:00:00Z', '2026-11-09T16:00:00Z')]
    const layout = layoutWeekEvents(events, week('2026-11-09'), 'Asia/Shanghai', { tasks: [due('due-midnight', '2026-11-09T16:00:00Z')] })
    expect(layout.days[0]).toMatchObject({ date: '2026-11-09', start: Date.parse('2026-11-08T16:00:00Z'), hours: 24 })
    expect(segments(layout, 'after-midnight')).toEqual([{ date: '2026-11-10', top: 0, bottom: 60, startsBefore: false, endsAfter: false, column: 0, columns: 1 }])
    expect(segments(layout, 'until-midnight')).toEqual([{ date: '2026-11-09', top: 1380, bottom: 1440, startsBefore: false, endsAfter: false, column: 0, columns: 1 }])
    // 截止在当地 0 点：算后一天
    expect(lane(layout, 'task:due-midnight')).toMatchObject({ startIndex: 1, span: 1 })
    const byDay = entriesByDay(events, [], week('2026-11-09'), 'Asia/Shanghai')
    expect(byDay.get('2026-11-09')!.map((entry) => entry.key)).toEqual(['event:until-midnight'])
    expect(byDay.get('2026-11-10')!.map((entry) => entry.key)).toEqual(['event:after-midnight'])
  })

  it('has a 25-hour day when New York falls back and draws the repeated hour side by side without drift', () => {
    const events = [
      timed('first-0130', '2026-11-01T05:30:00Z', '2026-11-01T06:00:00Z', 'America/New_York'),
      timed('second-0130', '2026-11-01T06:30:00Z', '2026-11-01T07:00:00Z', 'America/New_York'),
      timed('sunday-0900', '2026-11-01T14:00:00Z', '2026-11-01T15:00:00Z', 'America/New_York'),
      timed('friday-0900', '2026-10-30T13:00:00Z', '2026-10-30T14:00:00Z', 'America/New_York'),
    ]
    const layout = layoutWeekEvents(events, week('2026-10-26'), 'America/New_York')
    expect(layout.days.map((day) => day.hours)).toEqual([24, 24, 24, 24, 24, 24, 25])
    expect(layout.days[6]).toMatchObject({ date: '2026-11-01', start: Date.parse('2026-11-01T04:00:00Z'), end: Date.parse('2026-11-02T05:00:00Z') })
    // 挂钟都是 01:30：并排两列；第一条的结束挂钟（01:00）早于开始，按真实 30 分钟画高
    expect(segments(layout, 'first-0130')).toEqual([{ date: '2026-11-01', top: 90, bottom: 120, startsBefore: false, endsAfter: false, column: 0, columns: 2 }])
    expect(segments(layout, 'second-0130')).toMatchObject([{ top: 90, bottom: 120, column: 1, columns: 2 }])
    // 夏令时前后的 09:00 都在同一高度
    expect(segments(layout, 'sunday-0900')[0]).toMatchObject({ top: 540, bottom: 600 })
    expect(segments(layout, 'friday-0900')[0]).toMatchObject({ top: 540, bottom: 600 })
    expect(timedRange(events[0]!, 'America/New_York')).toBe('01:30（UTC−4） – 01:00（UTC−5）')
  })

  it('has a 23-hour day when New York springs forward', () => {
    const events = [timed('across-gap', '2026-03-08T06:30:00Z', '2026-03-08T07:30:00Z', 'America/New_York'), timed('sunday-1000', '2026-03-08T14:00:00Z', '2026-03-08T15:00:00Z'), timed('saturday-1000', '2026-03-07T15:00:00Z', '2026-03-07T16:00:00Z')]
    const layout = layoutWeekEvents(events, week('2026-03-02'), 'America/New_York')
    expect(layout.days.map((day) => day.hours)).toEqual([24, 24, 24, 24, 24, 24, 23])
    // 01:30 EST 到 03:30 EDT：真实 1 小时，按挂钟画在 01:30–03:30
    expect(segments(layout, 'across-gap')[0]).toMatchObject({ date: '2026-03-08', top: 90, bottom: 210 })
    expect(segments(layout, 'sunday-1000')[0]).toMatchObject({ top: 600, bottom: 660 })
    expect(segments(layout, 'saturday-1000')[0]).toMatchObject({ top: 600, bottom: 660 })
    expect(dayFrames(week('2026-03-02'), 'America/New_York')[6]).toMatchObject({ start: Date.parse('2026-03-08T05:00:00Z'), end: Date.parse('2026-03-09T04:00:00Z') })
  })

  it('splits timed events across New Year and across several days', () => {
    const events = [timed('new-year', '2026-12-31T14:00:00Z', '2026-12-31T18:00:00Z'), allDay('holiday', '2026-12-31', '2027-01-02', 'Asia/Shanghai', '2026-12-30T16:00:00Z', '2027-01-01T16:00:00Z'), allDay('earlier', '2026-12-25', '2026-12-30', 'Asia/Shanghai', '2026-12-24T16:00:00Z', '2026-12-29T16:00:00Z')]
    const layout = layoutWeekEvents(events, week('2026-12-28'), 'Asia/Shanghai')
    expect(layout.days.map((day) => day.date)).toEqual(['2026-12-28', '2026-12-29', '2026-12-30', '2026-12-31', '2027-01-01', '2027-01-02', '2027-01-03'])
    expect(segments(layout, 'new-year')).toEqual([
      { date: '2026-12-31', top: 1320, bottom: 1440, startsBefore: false, endsAfter: true, column: 0, columns: 1 },
      { date: '2027-01-01', top: 0, bottom: 120, startsBefore: true, endsAfter: false, column: 0, columns: 1 },
    ])
    expect(lane(layout, 'event:holiday')).toMatchObject({ startIndex: 3, span: 2, startsBefore: false, endsAfter: false })
    expect(lane(layout, 'event:earlier')).toMatchObject({ startIndex: 0, span: 2, startsBefore: true })
    expect(rangeTitle('2026-12-28', '2027-01-03')).toBe('2026年12月28日—2027年1月3日')
    expect(rangeTitle('2026-11-30', '2026-12-06')).toBe('2026年11月30日—12月6日')
    expect(rangeTitle('2026-11-09', '2026-11-15')).toBe('2026年11月9日—15日')

    const multi = timed('three-days', '2026-11-09T12:00:00Z', '2026-11-11T04:00:00Z')
    expect(segments(layoutWeekEvents([multi], week('2026-11-09'), 'Asia/Shanghai'), 'three-days')).toEqual([
      { date: '2026-11-09', top: 1200, bottom: 1440, startsBefore: false, endsAfter: true, column: 0, columns: 1 },
      { date: '2026-11-10', top: 0, bottom: 1440, startsBefore: true, endsAfter: true, column: 0, columns: 1 },
      { date: '2026-11-11', top: 0, bottom: 720, startsBefore: true, endsAfter: false, column: 0, columns: 1 },
    ])
  })

  it('shows all-day events on their own civil dates with an exclusive end, never by adding 24 hours to UTC', () => {
    const oneDay = allDay('one-day', '2026-11-09', '2026-11-10', 'Asia/Shanghai', '2026-11-08T16:00:00Z', '2026-11-09T16:00:00Z')
    // 基里巴斯（UTC+14）的 11 月 10 日，在檀香山（UTC−10）显示：UTC 开始时刻落在 11 月 9 日 0 点，仍画在 10 日
    const kiritimati = allDay('kiritimati', '2026-11-10', '2026-11-11', 'Pacific/Kiritimati', '2026-11-09T10:00:00Z', '2026-11-10T10:00:00Z')
    // 纽约冬令时切换那天的全天日程有 25 小时；只占 11 月 1 日一天
    const longDay = allDay('long-day', '2026-11-01', '2026-11-02', 'America/New_York', '2026-11-01T04:00:00Z', '2026-11-02T05:00:00Z')
    const events = Object.freeze([Object.freeze(oneDay), Object.freeze(kiritimati)])
    const before = JSON.stringify(events)
    const layout = layoutWeekEvents(events, week('2026-11-09'), 'Pacific/Honolulu')
    expect(lane(layout, 'event:one-day')).toMatchObject({ startIndex: 0, span: 1 })
    expect(lane(layout, 'event:kiritimati')).toMatchObject({ startIndex: 1, span: 1 })
    expect(layout.days.flatMap((day) => day.segments)).toEqual([])
    expect(JSON.stringify(events)).toBe(before)
    const byDay = entriesByDay(events, [], week('2026-11-09'), 'Pacific/Honolulu')
    expect(byDay.get('2026-11-09')!.map((entry) => entry.key)).toEqual(['event:one-day'])
    expect(byDay.get('2026-11-10')!.map((entry) => entry.key)).toEqual(['event:kiritimati'])
    expect(byDay.get('2026-11-11')).toEqual([])
    expect(lane(layoutWeekEvents([longDay], week('2026-10-26'), 'Asia/Shanghai'), 'event:long-day')).toMatchObject({ startIndex: 6, span: 1, endsAfter: false })
    expect(lane(layoutWeekEvents([longDay], week('2026-11-02'), 'Asia/Shanghai'), 'event:long-day')).toBeUndefined()
  })
})

describe('周视图布局：重叠与折叠', () => {
  const at = (id: string, from: string, to: string) => timed(id, `2026-11-10T${from}:00+08:00`, `2026-11-10T${to}:00+08:00`)
  it('places two and three overlapping events in columns and leaves adjacent events alone', () => {
    const two = layoutWeekEvents([at('a', '09:00', '10:00'), at('b', '09:30', '10:30')], week('2026-11-09'), 'Asia/Shanghai')
    expect([...segments(two, 'a'), ...segments(two, 'b')].map((s) => [s.column, s.columns])).toEqual([[0, 2], [1, 2]])
    const three = layoutWeekEvents([at('a', '09:00', '11:00'), at('b', '09:30', '10:30'), at('c', '10:00', '11:00'), at('d', '11:00', '12:00')], week('2026-11-09'), 'Asia/Shanghai')
    expect(['a', 'b', 'c', 'd'].map((id) => segments(three, id).map((s) => [s.column, s.columns])[0])).toEqual([[0, 3], [1, 3], [2, 3], [0, 1]])
    // 太短的日程按最短高度参与重叠判断，画出来不会盖住紧挨着的下一条
    const short = layoutWeekEvents([at('short', '09:00', '09:05'), at('next', '09:10', '09:40')], week('2026-11-09'), 'Asia/Shanghai')
    expect(segments(short, 'short')[0]).toMatchObject({ top: 540, bottom: 560, columns: 2 })
  })

  it('collapses overlaps beyond the column limit into “另有 N 项” without losing any', () => {
    const five = ['a', 'b', 'c', 'd', 'e'].map((id) => at(id, '09:00', '10:00'))
    const layout = layoutWeekEvents(five, week('2026-11-09'), 'Asia/Shanghai', { maxColumns: 3 })
    const day = layout.days[1]!
    expect(day.segments.map((s) => [s.event.id, s.column, s.columns])).toEqual([['a', 0, 3], ['b', 1, 3]])
    expect(day.collapsed).toEqual([expect.objectContaining({ column: 2, columns: 3, top: 540, events: five.slice(2) })])
  })

  it('keeps all of 121 events and 121 deadlines on one day (none of the last 21 dropped)', () => {
    const events = Array.from({ length: 121 }, (_, index) => at(`e${index + 1}`, '09:00', '10:00'))
    const tasks = Array.from({ length: 121 }, (_, index) => due(`t${index + 1}`, '2026-11-10T09:00:00Z'))
    const layout = layoutWeekEvents(events, week('2026-11-09'), 'Asia/Shanghai', { tasks })
    const day = layout.days[1]!
    const shown = [...day.segments.map((s) => s.event.id), ...day.collapsed.flatMap((group) => group.events.map((event) => event.id))]
    expect(shown.sort()).toEqual(events.map((event) => event.id).sort())
    expect(layout.lane.filter((item) => item.kind === 'task')).toHaveLength(3)
    expect(layout.laneHidden[1]).toBe(118)
    const byDay = entriesByDay(events, tasks, week('2026-11-09'), 'Asia/Shanghai').get('2026-11-10')!
    expect(byDay.filter((entry) => entry.kind === 'event')).toHaveLength(121)
    expect(byDay.filter((entry) => entry.kind === 'task')).toHaveLength(121)
  })
})

describe('日历按区间读完所有分页', () => {
  const path = (r: Request) => new URL(r.url).pathname
  const many = Array.from({ length: 121 }, (_, index) => ({ ...calendarEvent, id: `event-${index + 1}`, title: `日程 ${index + 1}`, start_at: `2026-11-10T0${index % 2}:${String(index % 60).padStart(2, '0')}:00Z`, end_at: '2026-11-10T03:00:00Z' }))
  const tasks = Array.from({ length: 121 }, (_, index) => ({ ...task, id: `task-${index + 1}`, title: `截止任务 ${index + 1}`, due_at: '2026-11-11T09:00:00Z' }))
  function pages<T>(items: T[], r: Request, extra: object = {}) {
    const cursor = new URL(r.url).searchParams.get('cursor'), offset = cursor ? Number(cursor.slice(5)) : 0, limit = Number(new URL(r.url).searchParams.get('limit'))
    return Response.json({ items: items.slice(offset, offset + limit), next_cursor: offset + limit < items.length ? `page-${offset + limit}` : null, total_estimate: null, ...extra })
  }
  async function open() {
    await openApp('/calendar?date=2026-11-10')
    await fireEvent.update(await screen.findByLabelText('显示时区'), 'Asia/Shanghai')
    await fireEvent.click(screen.getByRole('button', { name: '应用日历范围' }))
  }

  it('loads every page of events and task deadlines (121 each)', async () => {
    const requests = mockApi((r) => path(r) === '/api/v1/calendar/events' ? pages(many, r) : path(r) === '/api/v1/tasks' ? pages(tasks, r, { sync }) : undefined)
    await open()
    await fireEvent.click(screen.getByRole('button', { name: '日程列表' }))
    expect(await screen.findByRole('button', { name: '日程 121' })).toBeVisible()
    const day = screen.getByRole('region', { name: '11月11日 周三' })
    await vi.waitFor(() => expect(within(day).getAllByRole('article', { name: /^任务截止：截止任务/ })).toHaveLength(121))
    expect(within(day).getByRole('link', { name: '截止任务 121' })).toHaveAttribute('href', '/tasks?task=task-121')
    expect(screen.getAllByRole('button', { name: /^日程 \d+$/ })).toHaveLength(121)
    const eventReads = requests.filter((r) => path(r) === '/api/v1/calendar/events').map((r) => new URL(r.url).searchParams)
    expect(eventReads.map((params) => [params.get('limit'), params.get('cursor')])).toContainEqual(['100', 'page-100'])
    const taskReads = requests.filter((r) => path(r) === '/api/v1/tasks').map((r) => new URL(r.url).searchParams)
    expect(taskReads.map((params) => params.get('cursor'))).toContain('page-100')
    expect(taskReads.every((params) => params.get('status') === 'all' && params.get('due_after') && params.get('due_before'))).toBe(true)
    expect(screen.queryByText(/未加载完/)).not.toBeInTheDocument()
  })

  it('says the range is not fully loaded when a later page fails, and continues on request', async () => {
    let broken = true
    mockApi((r) => {
      if (path(r) !== '/api/v1/calendar/events') return
      if (broken && new URL(r.url).searchParams.get('cursor')) return fail(503, 'HTTP_ERROR')
      return pages(many, r)
    })
    await open()
    const warning = await screen.findByText(/日程未加载完：已加载 100 条/)
    expect(warning).toHaveAttribute('role', 'alert')
    // 第一页已经显示，没有被错误清掉
    await fireEvent.click(screen.getByRole('button', { name: '日程列表' }))
    expect(screen.getByRole('button', { name: '日程 100' })).toBeVisible()
    expect(screen.queryByRole('button', { name: '日程 121' })).not.toBeInTheDocument()
    broken = false
    await fireEvent.click(screen.getByRole('button', { name: '继续加载日程' }))
    expect(await screen.findByRole('button', { name: '日程 121' })).toBeVisible()
    await vi.waitFor(() => expect(screen.queryByText(/未加载完/)).not.toBeInTheDocument())
  })
})
