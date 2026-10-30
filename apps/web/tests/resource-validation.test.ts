import { describe, expect, it } from 'vitest'
import { sourceUrlError, bookmarkUrlError } from '../src/features/knowledge/validation'
import { addDays, dateInZone, localDateTime, zonedInstant, weekDates, eventDates } from '../src/features/calendar/time'
import { createApi } from '../src/services/api'
import { mockApi, emptyPage } from './helpers'
import { calendarEvent } from './sprint4-fixtures'

describe('知识入口 URL 校验', () => {
  it.each(['javascript:alert(1)', 'https://user:secret@docs.example.com', 'http://169.254.169.254', 'http://[fe80::1]', 'http://0.0.0.0', 'https://docs.example.com?token=x'])('rejects unsafe source %s', (url) => {
    expect(sourceUrlError(url)).not.toBe('')
  })
  it('accepts explicit development hosts and a deployment subpath', () => {
    expect(sourceUrlError('http://localhost:3010/affine')).toBe('')
    expect(bookmarkUrlError('https://docs.example.com/affine/workspace/ws-1/doc_1?mode=page', 'https://docs.example.com/affine')).toBe('')
  })
  it.each(['https://evil.example.com/affine/workspace/ws/doc', 'https://docs.example.com/affine-evil/workspace/ws/doc', 'https://docs.example.com/affine/workspace/ws/settings', 'https://docs.example.com/affine/workspace/ws/doc/extra', 'https://docs.example.com/affine/workspace/ws/a%2Fb', 'javascript:alert(1)'])('rejects invalid document %s', (url) => {
    expect(bookmarkUrlError(url, 'https://docs.example.com/affine')).not.toBe('')
  })
})

describe('日历的时区和日期边界', () => {
  it('converts timed inputs in their declared timezone rather than browser timezone', () => {
    expect(zonedInstant('2026-10-30T09:30', 'Asia/Shanghai')).toBe('2026-10-30T01:30:00.000Z')
    expect(localDateTime('2026-10-30T01:30:00Z', 'Asia/Shanghai')).toBe('2026-10-30T09:30')
    expect(dateInZone('2026-10-30T01:30:00Z', 'America/New_York')).toBe('2026-10-29')
  })
  it('rejects nonexistent local times, malformed dates and invalid timezone names', () => {
    expect(() => zonedInstant('2026-03-08T02:30', 'America/New_York')).toThrow()
    expect(() => zonedInstant('2026-02-30T09:00', 'UTC')).toThrow()
    expect(() => zonedInstant('2026-10-30T09:00', 'Invalid/Zone')).toThrow()
  })
  it('uses a deterministic earlier instant for an ambiguous fall-back time', () => {
    expect(zonedInstant('2026-11-01T01:30', 'America/New_York')).toBe('2026-11-01T05:30:00.000Z')
  })
  it('navigates dates without browser DST arithmetic', () => {
    expect(addDays('2026-03-08', 1)).toBe('2026-03-09')
    expect(weekDates('2026-11-01')).toEqual(['2026-10-26', '2026-10-27', '2026-10-28', '2026-10-29', '2026-10-30', '2026-10-31', '2026-11-01'])
  })
  it('groups multi-day all-day events without including the exclusive end', () => {
    expect(eventDates({ ...calendarEvent, all_day: true, start_date: '2026-10-29', end_date: '2026-10-31' }, 'Asia/Shanghai')).toEqual(['2026-10-29', '2026-10-30'])
    expect(eventDates({ ...calendarEvent, start_at: '2026-10-29T15:30:00Z', end_at: '2026-10-29T16:30:00Z' }, 'Asia/Shanghai')).toEqual(['2026-10-29', '2026-10-30'])
    expect(eventDates({ ...calendarEvent, start_at: '2026-10-29T15:30:00Z', end_at: '2026-10-29T16:00:00Z' }, 'Asia/Shanghai')).toEqual(['2026-10-29'])
  })
})

it('adds the selected workspace header to knowledge and calendar reads', async () => {
  const requests = mockApi(() => Response.json(emptyPage))
  const api = createApi({ workspaceId: () => 'workspace-a' })
  // Fault injection: untyped callers can omit the header required by OpenAPI.
  const header = {} as { 'X-Workspace-Id': string }
  await api.GET('/api/v1/knowledge/sources', { params: { header } })
  await api.GET('/api/v1/knowledge/resources', { params: { header } })
  await api.GET('/api/v1/calendar/events', { params: { header, query: { start: '2026-10-01T00:00:00Z', end: '2026-11-01T00:00:00Z' } } })
  expect(requests.map((r) => r.headers.get('X-Workspace-Id'))).toEqual(['workspace-a', 'workspace-a', 'workspace-a'])
})
