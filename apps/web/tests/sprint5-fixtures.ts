import type { Schema } from '../src/services/api'
export const at = '2026-10-31T04:00:00Z'
export const notification: Schema['NotificationOut'] = { id: 'notice-a', type: 'future_type', severity: 'warning', title: '服务告警', body: '<img src=x onerror=alert(1)>纯文本', link: '/operations', read_at: null, created_at: at }
export const notifications: Schema['NotificationPage'] = { items: [notification], unread_count: 2, next_cursor: null, total_estimate: 1 }
export const path = (r: Request) => new URL(r.url).pathname
