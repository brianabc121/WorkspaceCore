const { chromium } = require('../../../tests/e2e/node_modules/playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const baseUrl = process.env.WEB_BASE_URL || 'http://127.0.0.1:5173';
const screenshots = path.resolve(__dirname, '../docs/screenshots');
fs.mkdirSync(screenshots, { recursive: true });
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined, headless: true, args: ['--no-sandbox'] });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  const time = '2026-10-30T00:00:00Z';
  const project = { id: '66666666-6666-4666-8666-666666666666', integration_id: '77777777-7777-4777-8777-777777777777', external_id: 'vikunja:project:7', title: '发布计划', archived: false, source_url: 'https://tasks.example.com/projects/7', source_updated_at: time, synced_at: time };
  const sync = { status: 'ok', as_of: time, reason: null };
  const connection = { id: project.integration_id, provider: 'vikunja', name: 'Vikunja 团队任务', base_url: 'http://private-vikunja:3456', public_url: 'https://tasks.example.com', status: 'active', token_set: true, token_expires_at: null, tested_version: '2.7.1', api_version: 'v2', instance_version: '2.7.1', last_tested_at: time, sync, webhooks: { mode: 'auto', registered_projects: 1, reason: null }, version: 1, created_at: time, updated_at: time };
  const sample = { id: '88888888-8888-4888-8888-888888888888', integration_id: project.integration_id, project_id: project.id, external_id: 'vikunja:task:42', identifier: 'REL-42', title: '准备发布', description: '说明按纯文本显示 <b>不会渲染 HTML</b>', done: false, done_at: null, priority: 3, due_at: time, labels: ['发布'], source_url: 'https://tasks.example.com/tasks/42', version: 'opaque-original', source_updated_at: time, synced_at: time };
  let tasks = [sample, { ...sample, id: '99999999-9999-4999-8999-999999999999', identifier: 'REL-43', title: '检查发布说明', done: true, priority: 1 }];
  const writes = [];
  let createCount = 0;
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request(), url = new URL(request.url()), pathname = url.pathname;
    let body, status = 200;
    if (!['GET', 'HEAD'].includes(request.method())) {
      assert.equal(request.headers()['x-workspace-id'], 'workspace-a');
      assert.equal(request.headers()['x-csrf-token'], 'browser-csrf');
      assert.match(request.headers()['idempotency-key'], /^[0-9a-f-]{36}$/);
      writes.push({ path: pathname, method: request.method(), key: request.headers()['idempotency-key'], body: request.postDataJSON() });
    }
    if (pathname === '/api/v1/setup/status') body = { setup_required: false };
    else if (pathname === '/api/v1/me') body = { user: { id: 'user-a', display_name: '演示用户', email: 'test@example.com', created_at: time }, workspaces: [{ id: 'workspace-a', name: '个人工作空间', role: 'owner', created_at: time, updated_at: time }] };
    else if (pathname === '/api/v1/projects') body = { items: [project], sync, next_cursor: null, total_estimate: 1 };
    else if (pathname === '/api/v1/knowledge/resources') body = { items: [], next_cursor: null, total_estimate: null };
    else if (pathname === '/api/v1/tasks' && request.method() === 'GET') body = { items: tasks, sync, next_cursor: null, total_estimate: tasks.length };
    else if (pathname === '/api/v1/tasks' && request.method() === 'POST') {
      createCount++;
      if (createCount === 1) { status = 504; body = { error: { code: 'RESULT_UNKNOWN', message: '结果未知', request_id: 'mock', details: {} } }; }
      else {
        assert.equal(writes.at(-1).key, writes.at(-2).key);
        assert.deepEqual(writes.at(-1).body, writes.at(-2).body);
        const item = { ...sample, ...request.postDataJSON(), id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', identifier: 'REL-44' };
        tasks.push(item); body = item; status = 201;
      }
    } else if (pathname.startsWith('/api/v1/tasks/')) {
      const item = tasks.find((t) => t.id === pathname.split('/').at(-1));
      assert(item);
      if (request.method() === 'PATCH') { assert.equal(request.postDataJSON().version, item.version); Object.assign(item, request.postDataJSON(), { version: 'opaque-updated' }); }
      if (request.method() === 'DELETE') { assert.equal(url.searchParams.get('version'), item.version); tasks = tasks.filter((t) => t.id !== item.id); status = 204; }
      body = item;
    } else if (pathname === '/api/v1/integrations') body = { items: [connection], next_cursor: null, total_estimate: 1 };
    else if (pathname.endsWith('/test')) body = { ok: false, status: 'unauthorized', instance_version: '2.7.1', api_version: 'v2', tested_version: '2.7.1', compatible: true, checks: [{ name: 'reachable', ok: true, code: null }, { name: 'token', ok: false, code: 'INTEGRATION_UNAUTHORIZED' }], warnings: [] };
    else if (pathname === '/api/v1/integrations/' + connection.id && request.method() === 'PATCH') { assert.equal(request.postDataJSON().api_token, 'tk_browser-test'); body = { ...connection, version: 2 }; }
    else if (pathname.endsWith('/sync')) { body = { integration_id: connection.id, mode: 'full', queued_at: time }; status = 202; }
    else throw new Error(`Unexpected ${request.method()} ${pathname}`);
    await route.fulfill({ status, contentType: 'application/json', body: status === 204 ? undefined : JSON.stringify(body) });
  });
  await context.addCookies([{ name: 'ws_csrf', value: 'browser-csrf', url: baseUrl }]);
  await page.goto(baseUrl + '/tasks');
  await page.getByRole('button', { name: '准备发布', exact: true }).waitFor();
  await page.getByRole('button', { name: '新建任务', exact: true }).click();
  await page.getByLabel('任务标题', { exact: true }).fill('浏览器任务');
  await page.getByLabel('任务项目', { exact: true }).selectOption(project.id);
  await page.getByRole('button', { name: '创建任务', exact: true }).click();
  await page.getByText('操作结果未知，请查询原请求的结果，不要再次新建。', { exact: true }).waitFor();
  assert(await page.getByRole('button', { name: '创建任务', exact: true }).isDisabled());
  await page.getByRole('button', { name: '关闭对话框' }).click();
  await page.getByRole('button', { name: '查询原请求结果' }).click();
  await page.getByRole('button', { name: '浏览器任务', exact: true }).waitFor();
  assert.equal(createCount, 2);
  await page.getByRole('button', { name: '浏览器任务', exact: true }).click();
  await page.getByLabel('任务标题', { exact: true }).fill('浏览器任务更新');
  await page.getByRole('button', { name: '保存任务', exact: true }).click();
  await page.getByRole('button', { name: '浏览器任务更新', exact: true }).waitFor();
  await page.getByRole('button', { name: '看板', exact: true }).click();
  await page.screenshot({ path: path.join(screenshots, 'sprint3-tasks.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.evaluate(() => document.activeElement?.blur());
  await page.screenshot({ path: path.join(screenshots, 'sprint3-tasks-mobile.png'), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole('link', { name: '集成设置', exact: true }).click();
  await page.getByRole('button', { name: '修改连接' }).click();
  assert.equal(await page.getByLabel('服务地址', { exact: true }).inputValue(), '');
  assert.equal(await page.getByLabel('API Token', { exact: true }).inputValue(), '');
  assert(!(await page.locator('body').textContent()).includes(connection.base_url));
  await page.getByLabel('API Token', { exact: true }).fill('tk_browser-test');
  await page.getByRole('button', { name: '保存连接', exact: true }).click();
  await page.getByText('连接已保存，请测试连接', { exact: true }).waitFor();
  assert(!(await page.locator('body').textContent()).includes('tk_browser-test'));
  await page.getByRole('button', { name: '测试连接', exact: true }).click();
  await page.getByRole('heading', { name: '连接测试未通过' }).waitFor();
  await page.getByRole('button', { name: '手动同步', exact: true }).click();
  await page.getByText('同步已排队，请稍后刷新状态', { exact: true }).waitFor();
  await page.screenshot({ path: path.join(screenshots, 'sprint3-integrations.png'), fullPage: true });
  assert.deepEqual(errors, []);
  console.log('PASS: task list/board, unknown create + same-key lookup, versioned edit, CSRF/scope, mobile layout, credential no-echo, failed connection checks, manual sync; no page errors.');
  await browser.close();
})().catch((error) => { console.error(error); process.exit(1); });
