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
  // 工作台自己的项目与任务（T-0082 起，之前是 Vikunja 投影）
  const project = { id: '66666666-6666-4666-8666-666666666666', title: '发布计划', description: '', archived: false, open_tasks: 1, done_tasks: 1, version: 1, created_at: time, updated_at: time };
  let projects = [project];
  const sample = { id: '88888888-8888-4888-8888-888888888888', project_id: project.id, title: '准备发布', description: '说明按纯文本显示 <b>不会渲染 HTML</b>', done: false, done_at: null, priority: 3, due_at: time, labels: ['发布'], version: 'opaque-original', created_at: time, updated_at: time };
  let tasks = [sample, { ...sample, id: '99999999-9999-4999-8999-999999999999', title: '检查发布说明', done: true, done_at: time, priority: 1 }];
  const writes = [];
  let createCount = 0;
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request(), url = new URL(request.url()), pathname = url.pathname;
    let body, status = 200;
    if (!['GET', 'HEAD'].includes(request.method())) {
      assert.equal(request.headers()['x-workspace-id'], 'workspace-a');
      assert.equal(request.headers()['x-csrf-token'], 'browser-csrf');
      if (pathname.startsWith('/api/v1/tasks')) assert.match(request.headers()['idempotency-key'], /^[0-9a-f-]{36}$/);
      writes.push({ path: pathname, method: request.method(), key: request.headers()['idempotency-key'], body: request.postDataJSON() });
    }
    if (pathname === '/api/v1/setup/status') body = { setup_required: false };
    else if (pathname === '/api/v1/me') body = { user: { id: 'user-a', display_name: '演示用户', email: 'test@example.com', created_at: time }, workspaces: [{ id: 'workspace-a', name: '个人工作空间', role: 'owner', created_at: time, updated_at: time }] };
    else if (pathname === '/api/v1/projects' && request.method() === 'GET') body = { items: projects, next_cursor: null, total_estimate: null };
    else if (pathname === '/api/v1/projects' && request.method() === 'POST') {
      body = { ...project, ...request.postDataJSON(), id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', open_tasks: 0, done_tasks: 0 };
      projects = [...projects, body]; status = 201;
    }
    else if (pathname === '/api/v1/knowledge/resources') body = { items: [], next_cursor: null, total_estimate: null };
    // 顶栏的消息角标与待审批数（V2，与本测试无关）
    else if (pathname === '/api/v1/im/badges') body = { im_unread: 0, pending_approvals: 0 };
    else if (pathname === '/api/v1/ai/tool-executions') body = { items: [], next_cursor: null, total_estimate: null, pending_count: 0 };
    else if (pathname === '/api/v1/notifications') body = { items: [], next_cursor: null, total_estimate: null, unread_count: 0 };
    else if (pathname === '/api/v1/events/stream') { await route.abort(); return; }
    else if (pathname === '/api/v1/tasks' && request.method() === 'GET') body = { items: tasks, next_cursor: null, total_estimate: tasks.length };
    else if (pathname === '/api/v1/tasks' && request.method() === 'POST') {
      createCount++;
      if (createCount === 1) { status = 504; body = { error: { code: 'RESULT_UNKNOWN', message: '结果未知', request_id: 'mock', details: {} } }; }
      else {
        assert.equal(writes.at(-1).key, writes.at(-2).key);
        assert.deepEqual(writes.at(-1).body, writes.at(-2).body);
        const item = { ...sample, ...request.postDataJSON(), id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' };
        tasks.push(item); body = item; status = 201;
      }
    } else if (pathname.startsWith('/api/v1/tasks/')) {
      const item = tasks.find((t) => t.id === pathname.split('/').at(-1));
      assert(item);
      if (request.method() === 'PATCH') { assert.equal(request.postDataJSON().version, item.version); Object.assign(item, request.postDataJSON(), { version: 'opaque-updated' }); }
      if (request.method() === 'DELETE') { assert.equal(url.searchParams.get('version'), item.version); tasks = tasks.filter((t) => t.id !== item.id); status = 204; }
      body = item;
    }
    else if (pathname === '/api/v1/im/status') body = { available: false, account_ready: false, im_user_id: null };
    // 与本测试无关的读接口（顶栏、侧栏的其他模块）：空列表；写接口必须是上面列出的
    else if (request.method() === 'GET') body = { items: [], next_cursor: null, total_estimate: null };
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
  await page.getByRole('link', { name: '项目', exact: true }).click();
  await page.getByRole('region', { name: '项目 发布计划' }).waitFor();
  assert(!(await page.locator('body').textContent()).includes('Vikunja'));
  await page.getByRole('button', { name: '新建项目', exact: true }).click();
  await page.getByLabel('项目名称', { exact: true }).fill('浏览器项目');
  await page.getByRole('button', { name: '创建项目', exact: true }).click();
  await page.getByText('已新建项目「浏览器项目」', { exact: true }).waitFor();
  await page.getByRole('region', { name: '项目 浏览器项目' }).waitFor();
  assert.deepEqual(writes.at(-1), { path: '/api/v1/projects', method: 'POST', key: undefined, body: { title: '浏览器项目', description: '' } });
  await page.screenshot({ path: path.join(screenshots, 'sprint3-projects.png'), fullPage: true });
  assert.deepEqual(errors, []);
  console.log('PASS: task list/board, unknown create + same-key lookup, versioned edit, CSRF/scope, mobile layout, project page create; no page errors.');
  await browser.close();
})().catch((error) => { console.error(error); process.exit(1); });
