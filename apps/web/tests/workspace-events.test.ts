import { describe, it, expect, vi } from 'vitest'
import { screen, fireEvent } from '@testing-library/vue'
import { mockApi, openApp, me, workspace } from './helpers'
import { services, metrics } from './operations-fixtures'
import { notifications, path } from './sprint5-fixtures'
import { useAuthStore } from '../src/stores/auth'

class Stream extends EventTarget {
  static all: Stream[] = []
  readyState = 0; closed = false
  constructor(public url: string) { super(); Stream.all.push(this) }
  close() { this.closed = true; this.readyState = 2 }
  open() { this.readyState = 1; this.dispatchEvent(new Event('open')) }
  event(data: unknown) { this.dispatchEvent(new MessageEvent('invalidate', { data: typeof data === 'string' ? data : JSON.stringify(data) })) }
}
function setup() {
  Stream.all = []; vi.stubGlobal('EventSource', Stream)
  let revision = 0
  const requests = mockApi(r => {
    if (path(r) === '/api/v1/me') return Response.json({ ...me, workspaces: [workspace, { ...workspace, id: 'workspace-b', name: 'B' }] })
    if (path(r).endsWith('/operations/services')) return Response.json({ ...services, items: [{ ...services.items[0], name: 'API ' + revision }] })
    if (path(r).endsWith('/operations/metrics')) return Response.json(metrics)
    if (path(r) === '/api/v1/notifications') return Response.json({ ...notifications, unread_count: revision })
  })
  return { requests, change() { revision++ }, event(topics: string[], reset = false) { Stream.all.at(-1)!.event({ workspace_id: 'workspace-a', topics, reset, at: '2026-10-31T04:00:00Z' }) } }
}
describe('workspace events', () => {
  it('opens one same-origin stream and refetches current data on every open including reconnect', async () => {
    const model = setup(); await openApp('/operations'); await screen.findByRole('region', { name: 'API 0' }); expect(Stream.all).toHaveLength(1)
    expect(Stream.all).toHaveLength(1); expect(Stream.all[0]!.url).toBe('/api/v1/events/stream?workspace_id=workspace-a')
    model.change(); Stream.all[0]!.open(); await screen.findByRole('region', { name: 'API 1' })
    model.change(); Stream.all[0]!.readyState = 0; Stream.all[0]!.dispatchEvent(new Event('error')); Stream.all[0]!.open()
    await screen.findByRole('region', { name: 'API 2' }); expect(Stream.all).toHaveLength(1); expect(Stream.all[0]!.closed).toBe(false)
    expect(screen.queryByRole('alert', { name: /事件/ })).not.toBeInTheDocument()
  })
  it('invalidates operations including cached widgets but leaves unrelated queries unchanged', async () => {
    const model = setup(); const { queryClient } = await openApp('/operations'); await screen.findByRole('region', { name: 'API 0' }); expect(Stream.all).toHaveLength(1)
    queryClient.setQueryData(['operations', 'user-a', 'workspace-a', 'widget', 'services'], services)
    queryClient.setQueryData(['operations', 'user-a', 'workspace-b', 'widget', 'services'], services)
    const before = model.requests.filter(r => path(r) === '/api/v1/notifications').length
    model.change(); model.event(['operations', 'future_topic']); await screen.findByRole('region', { name: 'API 1' })
    expect(queryClient.getQueryState(['operations', 'user-a', 'workspace-a', 'widget', 'services'])?.isInvalidated).toBe(true)
    expect(queryClient.getQueryState(['operations', 'user-a', 'workspace-b', 'widget', 'services'])?.isInvalidated).toBe(false)
    expect(model.requests.filter(r => path(r) === '/api/v1/notifications')).toHaveLength(before)
  })
  it('refreshes all active data on reset and when a hidden page becomes visible', async () => {
    const model = setup(); await openApp('/operations'); await screen.findByRole('region', { name: 'API 0' }); expect(Stream.all).toHaveLength(1)
    model.change(); model.event([], true); await screen.findByRole('region', { name: 'API 1' }); await screen.findByRole('button', { name: '通知，1 条未读' })
    model.change(); vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible'); document.dispatchEvent(new Event('visibilitychange'))
    await screen.findByRole('region', { name: 'API 2' })
  })
  it('ignores malformed and other-scope events, closes old stream on switch/logout and ignores late events', async () => {
    const model = setup(); const { pinia, unmount } = await openApp('/operations'); await screen.findByRole('region', { name: 'API 0' }); expect(Stream.all).toHaveLength(1)
    const first = Stream.all[0]!, before = model.requests.length
    first.event('bad JSON'); first.event({ workspace_id: 'workspace-b', topics: ['operations'], at: 'now' }); first.event({ workspace_id: 'workspace-a', topics: null })
    await new Promise(resolve => setTimeout(resolve, 20)); expect(model.requests).toHaveLength(before)
    useAuthStore(pinia).selectWorkspace('workspace-b'); await vi.waitFor(() => expect(Stream.all).toHaveLength(2)); expect(first.closed).toBe(true)
    await screen.findByRole('region', { name: 'API 0' }); const count = model.requests.length; first.open(); first.event({ workspace_id: 'workspace-a', topics: ['operations'] })
    await new Promise(resolve => setTimeout(resolve, 20)); expect(model.requests).toHaveLength(count)
    useAuthStore(pinia).clearSession(); expect(Stream.all[1]!.closed).toBe(true); unmount()
  })
  it('keeps manual refresh usable when stream is unavailable or rejected before opening (including 429)', async () => {
    const model = setup(); await openApp('/operations'); await screen.findByRole('region', { name: 'API 0' }); expect(Stream.all).toHaveLength(1)
    Stream.all[0]!.readyState = 2; Stream.all[0]!.dispatchEvent(new Event('error'))
    model.change(); await fireEvent.click(screen.getByRole('button', { name: '刷新运维数据' })); await screen.findByRole('region', { name: 'API 1' })
    expect(Stream.all[0]!.closed).toBe(true); expect(Stream.all).toHaveLength(1)
  })
})
