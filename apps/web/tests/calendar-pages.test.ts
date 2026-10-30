import { fireEvent, screen } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'
import { mockApi, openApp, emptyPage, me, workspace, fail } from './helpers'
import { calendarEvent } from './sprint4-fixtures'
const path = (r: Request) => new URL(r.url).pathname
function calendar(override?: (r: Request) => Response | Promise<Response> | undefined) {
  return mockApi(async (r) => {
    const response = await override?.(r)
    if (response) return response
    if (path(r) === '/api/v1/calendar/events') return Response.json({ ...emptyPage, items: [calendarEvent] })
  })
}
async function openCalendar() {
  await openApp('/calendar')
  await fireEvent.update(await screen.findByLabelText('查看日期'), '2026-10-30')
  await fireEvent.update(screen.getByLabelText('显示时区'), 'Asia/Shanghai')
  await fireEvent.click(screen.getByRole('button', { name: '应用日历范围' }))
  await screen.findByRole('button', { name: calendarEvent.title })
}
describe('本地日历', () => {
  it('retains civil-date all-day events across extreme display timezones and filters padded results', async () => {
    const allDay = { ...calendarEvent, title: '跨日期边界的全天安排', all_day: true, timezone: 'Pacific/Kiritimati', start_date: '2026-11-02', end_date: '2026-11-03', start_at: '2026-11-01T10:00:00Z', end_at: '2026-11-02T10:00:00Z' }
    const outside = { ...calendarEvent, id: 'outside', title: '显示周之前的日程', start_at: '2026-11-01T20:00:00Z', end_at: '2026-11-01T21:00:00Z' }
    calendar((r) => {
      if (path(r) !== '/api/v1/calendar/events') return
      const params = new URL(r.url).searchParams, from = Date.parse(params.get('start')!), to = Date.parse(params.get('end')!)
      return Response.json({ ...emptyPage, items: [allDay, outside].filter((event) => Date.parse(event.end_at) > from && Date.parse(event.start_at) < to) })
    })
    await openApp('/calendar')
    await fireEvent.update(await screen.findByLabelText('查看日期'), '2026-11-02')
    await fireEvent.update(screen.getByLabelText('显示时区'), 'Pacific/Honolulu')
    await fireEvent.click(screen.getByRole('button', { name: '应用日历范围' }))
    expect(await screen.findByRole('button', { name: allDay.title })).toBeVisible()
    expect(screen.queryByRole('button', { name: outside.title })).not.toBeInTheDocument()
    await fireEvent.click(screen.getByRole('button', { name: '下一周' }))
    expect(await screen.findByText('此时间范围暂无日程。')).toBeVisible()
  })
  it('shows agenda and a week view with explicit timezone and navigable range', async () => {
    const requests = calendar(); await openCalendar()
    expect(screen.getByText(/09:30.*10:30.*Asia\/Shanghai/)).toBeVisible()
    await fireEvent.click(screen.getByRole('button', { name: '周视图' }))
    expect(screen.getAllByRole('region', { name: /^2026-/ })).toHaveLength(7)
    await fireEvent.click(screen.getByRole('button', { name: '下一周' }))
    // The visible week begins Nov 2; reads include two days of timezone padding.
    await vi.waitFor(() => expect(requests.filter((r) => path(r) === '/api/v1/calendar/events').some((r) => new URL(r.url).searchParams.get('start') === '2026-10-30T16:00:00.000Z')).toBe(true))
  })
  it('validates a timed event then sends UTC instants in the declared timezone', async () => {
    const requests = calendar((r) => r.method === 'POST' ? Response.json(calendarEvent, { status: 201 }) : undefined)
    await openCalendar(); await fireEvent.click(screen.getByRole('button', { name: '新建日程' }))
    await fireEvent.update(screen.getByLabelText('日程标题'), '跨时区评审')
    await fireEvent.update(screen.getByLabelText('事件时区'), 'Asia/Shanghai')
    await fireEvent.update(screen.getByLabelText('开始时间'), '2026-10-30T09:30')
    await fireEvent.update(screen.getByLabelText('结束时间'), '2026-10-30T09:00')
    await fireEvent.click(screen.getByRole('button', { name: '保存日程' }))
    expect(await screen.findByText(/结束.*晚于开始/)).toBeVisible()
    expect(requests.some((r) => r.method === 'POST')).toBe(false)
    await fireEvent.update(screen.getByLabelText('结束时间'), '2026-10-30T10:30')
    await fireEvent.click(screen.getByRole('button', { name: '保存日程' }))
    await vi.waitFor(() => expect(requests.some((r) => r.method === 'POST')).toBe(true))
    const body = await requests.find((r) => r.method === 'POST')!.clone().json()
    expect(body).toMatchObject({ title: '跨时区评审', timezone: 'Asia/Shanghai', start_at: '2026-10-30T01:30:00.000Z', end_at: '2026-10-30T02:30:00.000Z', all_day: false })
    expect(body).not.toHaveProperty('start_date')
  })
  it('creates all-day events with an exclusive end date and no timed fields', async () => {
    const requests = calendar((r) => r.method === 'POST' ? Response.json(calendarEvent, { status: 201 }) : undefined)
    await openCalendar(); await fireEvent.click(screen.getByRole('button', { name: '新建日程' }))
    await fireEvent.update(screen.getByLabelText('日程标题'), '全天发布')
    await fireEvent.click(screen.getByLabelText('全天事件'))
    await fireEvent.update(screen.getByLabelText('开始日期'), '2026-10-30')
    await fireEvent.update(screen.getByLabelText('结束日期（不含）'), '2026-10-31')
    await fireEvent.click(screen.getByRole('button', { name: '保存日程' }))
    await vi.waitFor(() => expect(requests.some((r) => r.method === 'POST')).toBe(true))
    const body = await requests.find((r) => r.method === 'POST')!.clone().json()
    expect(body).toMatchObject({ all_day: true, start_date: '2026-10-30', end_date: '2026-10-31' })
    expect(body).not.toHaveProperty('start_at'); expect(body).not.toHaveProperty('end_at')
  })
  it('sends versions on updates and requires confirmation for deletion', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const requests = calendar((r) => r.method === 'PATCH' ? Response.json(calendarEvent) : r.method === 'DELETE' ? new Response(null, { status: 204 }) : undefined)
    await openCalendar(); await fireEvent.click(screen.getByRole('button', { name: calendarEvent.title }))
    await fireEvent.update(screen.getByLabelText('日程标题'), '新的评审')
    await fireEvent.click(screen.getByRole('button', { name: '保存日程' }))
    await vi.waitFor(() => expect(requests.some((r) => r.method === 'PATCH')).toBe(true))
    expect(await requests.find((r) => r.method === 'PATCH')!.clone().json()).toMatchObject({ version: 3, title: '新的评审' })
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await fireEvent.click(screen.getByRole('button', { name: calendarEvent.title })); await fireEvent.click(screen.getByRole('button', { name: '删除日程' }))
    expect(requests.some((r) => r.method === 'DELETE')).toBe(false)
    confirm.mockReturnValue(true); await fireEvent.click(screen.getByRole('button', { name: '删除日程' }))
    await vi.waitFor(() => expect(requests.some((r) => r.method === 'DELETE')).toBe(true))
    expect(new URL(requests.find((r) => r.method === 'DELETE')!.url).searchParams.get('version')).toBe('3')
  })
  it.each([409, 502])('requires a read refresh after a conflicting or uncertain write (%s)', async (status) => {
    const requests = calendar((r) => r.method === 'POST' ? status === 409 ? fail(409, 'VERSION_CONFLICT') : new Response('gateway', { status: 502 }) : undefined)
    await openCalendar(); await fireEvent.click(screen.getByRole('button', { name: '新建日程' }))
    await fireEvent.update(screen.getByLabelText('日程标题'), '日程')
    await fireEvent.click(screen.getByRole('button', { name: '保存日程' }))
    await vi.waitFor(() => expect(screen.getByRole('button', { name: '保存日程' })).toBeDisabled())
    await fireEvent.click(screen.getByRole('button', { name: '重新加载日历' }))
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(requests.filter((r) => r.method === 'POST')).toHaveLength(1)
  })
  it('allows viewers to read event details without exposing write controls', async () => {
    calendar((r) => path(r) === '/api/v1/me' ? Response.json({ ...me, workspaces: [{ ...workspace, role: 'viewer' }] }) : undefined)
    await openCalendar(); await fireEvent.click(screen.getByRole('button', { name: calendarEvent.title }))
    expect(screen.queryByRole('button', { name: '新建日程' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '保存日程' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '删除日程' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('日程标题')).toBeDisabled()
  })
  it('clears cached events after access loss', async () => {
    let revoked = false
    calendar((r) => revoked && path(r) === '/api/v1/calendar/events' ? fail(403, 'FORBIDDEN') : undefined)
    await openCalendar(); revoked = true
    await fireEvent.click(screen.getByRole('button', { name: '刷新日历' }))
    await vi.waitFor(() => expect(screen.queryByRole('button', { name: calendarEvent.title })).not.toBeInTheDocument())
    expect(screen.getByRole('alert')).toHaveTextContent('没有查看')
  })
})
