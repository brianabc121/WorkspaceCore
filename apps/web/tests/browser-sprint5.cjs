// Notification and EventSource smoke with a contract-simulated BFF.
const { chromium } = require('../../../tests/e2e/node_modules/playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const baseUrl = process.env.WEB_BASE_URL || 'http://127.0.0.1:5197';
const shots = process.env.SCREENSHOT_DIR || path.resolve(__dirname, '../docs/screenshots');
(async () => {
  fs.mkdirSync(shots, { recursive: true });
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined, headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, timezoneId: 'Asia/Shanghai' });
    const errors = [], external = [], writes = [], requestedPaths = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('request', r => { if (new URL(r.url()).origin !== baseUrl) external.push(r.url()); });
    await page.addInitScript(() => {
      window.__streams = [];
      window.EventSource = class extends EventTarget {
        constructor(url) { super(); this.url = url; this.readyState = 0; window.__streams.push(this); setTimeout(() => { this.readyState = 1; this.dispatchEvent(new Event('open')); }, 0); }
        close() { this.readyState = 2; this.closed = true; }
      };
    });
    const at = '2026-10-31T04:00:00Z', empty = { items: [], next_cursor: null, total_estimate: 0 };
    let unread = 3, role = 'owner';
    const notification = { id: 'notice-a', type: 'future', severity: 'warning', title: '服务告警', body: '<b>纯文本告警</b>', link: '/operations', read_at: null, created_at: at };
    await page.route('**/api/v1/**', async route => {
      try {
        const request = route.request(), url = new URL(request.url()), p = url.pathname, method = request.method(); let body;
        requestedPaths.push(p);
        if (!['/api/v1/setup/status', '/api/v1/me'].includes(p)) assert.equal(request.headers()['x-workspace-id'], 'workspace-a');
        if (method !== 'GET') writes.push({ path: p, body: request.postDataJSON() });
        if (p === '/api/v1/setup/status') body = { setup_required: false };
        else if (p === '/api/v1/me') body = { user: { id: 'user-a', display_name: '演示用户', email: 'test@example.com', created_at: at }, workspaces: [{ id: 'workspace-a', name: '个人空间', role, created_at: at, updated_at: at }] };
        else if (p === '/api/v1/notifications') {
          if (method === 'PATCH') { unread = request.postDataJSON().all ? 0 : Math.max(0, unread - 1); body = { updated: 1, unread_count: unread }; }
          else body = { ...empty, unread_count: unread, items: unread ? [notification, { ...notification, id: 'notice-b', title: '未来通知', body: '普通通知', link: '//evil.example.test' }, { ...notification, id: 'notice-c', title: '历史通知', body: '已移除页面的旧链接', link: '/automations' }] : [] };
        }
        else if (p === '/api/v1/search') body = empty;
        else if (p === '/api/v1/im/badges') body = { im_unread: 0, pending_approvals: 0 };
        else if (p === '/api/v1/ai/tool-executions') body = { ...empty, pending_count: 0 };
        else throw new Error('Unexpected ' + method + ' ' + p);
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
      } catch (error) { errors.push(error.message); await route.fulfill({ status: 500, body: '{}' }); }
    });
    await page.goto(baseUrl + '/missing'); await page.getByRole('heading', { name: '页面未找到', exact: true }).waitFor();
    assert.equal(await page.getByRole('navigation', { name: '主导航' }).getByRole('link', { name: '自动化', exact: true }).count(), 0);
    await page.getByRole('button', { name: '通知，3 条未读' }).click();
    const list = page.getByRole('region', { name: '通知列表', exact: true }); await list.getByText('<b>纯文本告警</b>', { exact: true }).waitFor(); assert.equal(await list.locator('b').count(), 0); assert.equal(await list.getByRole('link', { name: '未来通知' }).count(), 0);
    await page.screenshot({ path: path.join(shots, 'sprint5-notifications.png'), fullPage: true });
    await list.getByRole('link', { name: '历史通知' }).click(); await page.waitForURL(baseUrl + '/automations'); await page.getByRole('heading', { name: '页面未找到', exact: true }).waitFor();
    await list.getByRole('button', { name: '标为已读：服务告警' }).click(); await page.getByRole('button', { name: '通知，2 条未读' }).waitFor(); await page.getByRole('button', { name: '全部标为已读' }).click(); await page.getByRole('button', { name: '通知，0 条未读' }).waitFor();
    await page.getByText('通知已标为已读。', { exact: true }).waitFor(); await page.keyboard.press('Escape'); await page.getByRole('dialog').waitFor({ state: 'detached' });
    assert.deepEqual(writes, [{ path: '/api/v1/notifications', body: { ids: ['notice-a'] } }, { path: '/api/v1/notifications', body: { all: true } }]);
    unread = 4; await page.evaluate(() => window.__streams.at(-1).dispatchEvent(new Event('open'))); await page.getByRole('button', { name: '通知，4 条未读' }).waitFor();
    assert.equal(await page.evaluate(() => window.__streams.length), 1);
    await page.getByRole('button', { name: '切换到暗色主题' }).click(); await page.getByRole('button', { name: '通知，4 条未读' }).click(); await list.waitFor(); await page.screenshot({ path: path.join(shots, 'sprint5-notifications-dark.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 }); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false); await page.screenshot({ path: path.join(shots, 'sprint5-notifications-mobile.png'), fullPage: true }); await page.keyboard.press('Escape');
    role = 'viewer'; await page.reload(); await page.getByRole('heading', { name: '页面未找到', exact: true }).waitFor(); await page.getByRole('button', { name: '通知，4 条未读' }).click(); await list.getByText('<b>纯文本告警</b>', { exact: true }).waitFor();
    assert.equal(requestedPaths.some(p => /^\/api\/v1\/(automations|app-logins)(\/|$)/.test(p)), false);
    assert.deepEqual(errors, []); assert.deepEqual(external, []);
    console.log('PASS: notification count/read/all/plain text/safe links, old notification uses missing-page view, SSE reconnect refetch, viewer notifications, light/dark/mobile; no retired API calls, page errors or external requests.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
