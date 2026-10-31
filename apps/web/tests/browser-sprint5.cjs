// Contract-simulated BFF and EventSource transport; real stack acceptance is T-0040.
const { chromium } = require('../../../tests/e2e/node_modules/playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const baseUrl = process.env.WEB_BASE_URL || 'http://127.0.0.1:5197';
const shots = path.resolve(__dirname, '../docs/screenshots');
(async () => {
  fs.mkdirSync(shots, { recursive: true });
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined, headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, timezoneId: 'Asia/Shanghai' });
    const errors = [], external = [], writes = [];
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
    let unread = 2, role = 'owner', title = '生成日报', triggered = false;
    const notification = { id: 'notice-a', type: 'future', severity: 'warning', title: '服务告警', body: '<b>纯文本告警</b>', link: '/operations', read_at: null, created_at: at };
    const run = { id: 'run-a', automation_id: 'flow-a', status: 'unknown', external_execution_id: null, triggered_by: 'user-a', error_summary: null, started_at: null, finished_at: null, created_at: at };
    const automation = () => ({ id: 'flow-a', name: title, description: '受控触发与执行状态', workflow_id: 'Report1', webhook_path: 'report', params: [{ name: 'recipient', label: '收件人', type: 'string', required: true, sensitive: true, max_length: 40 }, { name: 'count', label: '份数', type: 'number', required: true, sensitive: false }, { name: 'send', label: '发送', type: 'boolean', required: true, sensitive: false }], enabled: true, workflow_active: true, last_run: triggered ? run : null, version: 1, created_at: at, updated_at: at });
    await page.route('**/api/v1/**', async route => {
      try {
        const request = route.request(), url = new URL(request.url()), p = url.pathname, method = request.method(); let body;
        if (!['/api/v1/setup/status', '/api/v1/me'].includes(p)) assert.equal(request.headers()['x-workspace-id'], 'workspace-a');
        if (method !== 'GET') writes.push({ path: p, key: request.headers()['idempotency-key'], body: request.postDataJSON() });
        if (p === '/api/v1/setup/status') body = { setup_required: false };
        else if (p === '/api/v1/me') body = { user: { id: 'user-a', display_name: '演示用户', email: 'test@example.com', created_at: at }, workspaces: [{ id: 'workspace-a', name: '个人空间', role, created_at: at, updated_at: at }] };
        else if (p === '/api/v1/notifications') {
          if (method === 'PATCH') { unread = request.postDataJSON().all ? 0 : Math.max(0, unread - 1); body = { updated: 1, unread_count: unread }; }
          else body = { ...empty, unread_count: unread, items: unread ? [notification, { ...notification, id: 'notice-b', title: '未来通知', body: '普通通知', link: '//evil.example.test' }] : [] };
        } else if (p === '/api/v1/automations') body = { ...empty, items: [automation()], connection: { status: 'ok', as_of: at } };
        else if (p === '/api/v1/automations/connection') { assert.equal(role, 'owner'); body = { configured: true, base_url: 'http://n8n:5678', public_url: 'https://n8n.example.test', api_key_set: true, trigger_secret_set: true, version: 1, status: 'active', tested_version: '2.45.7', sync: { status: 'ok', as_of: at } }; }
        else if (p.endsWith('/trigger')) { assert.equal(role, 'owner'); triggered = true; body = run; }
        else if (p.endsWith('/runs')) body = { ...empty, items: triggered ? [run] : [] };
        else if (p === '/api/v1/search') body = empty;
        else throw new Error('Unexpected ' + method + ' ' + p);
        await route.fulfill({ status: p.endsWith('/trigger') ? 202 : 200, contentType: 'application/json', body: JSON.stringify(body) });
      } catch (error) { errors.push(error.message); await route.fulfill({ status: 500, body: '{}' }); }
    });
    await page.goto(baseUrl + '/automations'); await page.getByRole('region', { name: title, exact: true }).waitFor();
    await page.getByRole('button', { name: '通知，2 条未读' }).click();
    const list = page.getByRole('region', { name: '通知列表', exact: true }); await list.getByText('<b>纯文本告警</b>', { exact: true }).waitFor(); assert.equal(await list.locator('b').count(), 0); assert.equal(await list.getByRole('link', { name: '未来通知' }).count(), 0);
    await page.screenshot({ path: path.join(shots, 'sprint5-notifications.png'), fullPage: true });
    await list.getByRole('button', { name: '标为已读：服务告警' }).click(); await page.getByRole('button', { name: '通知，1 条未读' }).waitFor(); await page.getByRole('button', { name: '全部标为已读' }).click(); await page.getByRole('button', { name: '通知，0 条未读' }).waitFor();
    await page.getByText('通知已标为已读。', { exact: true }).waitFor(); await page.keyboard.press('Escape'); await page.getByRole('dialog').waitFor({ state: 'detached' });
    unread = 3; title = '更新后的日报'; await page.evaluate(() => window.__streams.at(-1).dispatchEvent(new Event('open'))); await page.getByRole('region', { name: title, exact: true }).waitFor(); await page.getByRole('button', { name: '通知，3 条未读' }).waitFor();
    assert.equal(await page.evaluate(() => window.__streams.length), 1);
    await page.getByRole('button', { name: '触发：' + title }).click();
    await page.getByLabel('收件人 *', { exact: true }).fill('sample@example.test'); await page.getByLabel('份数 *', { exact: true }).fill('2'); await page.getByLabel('发送 *', { exact: true }).selectOption('false'); await page.getByRole('button', { name: '检查并确认' }).click(); assert.equal(writes.filter(w => w.path.endsWith('/trigger')).length, 0);
    await page.getByRole('button', { name: '确认触发', exact: true }).click(); const dialog = page.getByRole('dialog'); await dialog.getByText('结果未知，正在确认', { exact: true }).waitFor(); await dialog.getByRole('button', { name: '核对运行记录' }).click(); await dialog.getByRole('table', { name: title + '运行记录' }).waitFor(); await dialog.getByRole('button', { name: '刷新运行记录' }).click(); await dialog.getByRole('button', { name: '已核对，开始新提交' }).waitFor();
    assert.equal(writes.filter(w => w.path.endsWith('/trigger')).length, 1); const submission = writes.find(w => w.path.endsWith('/trigger')); assert.match(submission.key, /^[0-9a-f-]{36}$/); assert.deepEqual(submission.body, { params: { recipient: 'sample@example.test', count: 2, send: false } }); assert.equal(await page.evaluate(() => JSON.stringify(sessionStorage).includes('sample@example.test')), false);
    await page.screenshot({ path: path.join(shots, 'sprint5-trigger-unknown.png'), fullPage: true }); await page.getByRole('button', { name: '关闭对话框' }).click(); await page.getByRole('button', { name: '查看运行记录' }).click(); await page.getByRole('table', { name: title + '运行记录' }).waitFor(); await page.screenshot({ path: path.join(shots, 'sprint5-automations.png'), fullPage: true });
    await page.getByRole('button', { name: '切换到暗色主题' }).click(); await page.screenshot({ path: path.join(shots, 'sprint5-automations-dark.png'), fullPage: true }); await page.setViewportSize({ width: 390, height: 844 }); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false); await page.screenshot({ path: path.join(shots, 'sprint5-automations-mobile.png'), fullPage: true });
    role = 'viewer'; await page.reload(); await page.getByRole('region', { name: title, exact: true }).waitFor(); assert.equal(await page.getByRole('button', { name: /触发：|配置 n8n|登记工作流|移出白名单/ }).count(), 0);
    assert.deepEqual(errors, []); assert.deepEqual(external, []);
    console.log('PASS: notification count/read/all/plain text/safe links, SSE reconnect refetch, typed confirmed trigger with UUID/unknown reconciliation/no duplicate or sensitive storage, viewer read-only, light/dark/mobile; no page errors or external requests.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
