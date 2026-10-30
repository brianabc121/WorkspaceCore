// Local UI smoke test with contract-shaped BFF responses; never contacts AFFiNE.
const { chromium } = require('../../../tests/e2e/node_modules/playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const baseUrl = process.env.WEB_BASE_URL || 'http://127.0.0.1:5173';
const screenshots = path.resolve(__dirname, '../docs/screenshots');
fs.mkdirSync(screenshots, { recursive: true });
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined, headless: true, args: ['--no-sandbox'] });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, timezoneId: 'Asia/Shanghai' });
  const page = await context.newPage(), errors = [], external = [], writes = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('request', (r) => { if (new URL(r.url()).origin !== baseUrl) external.push(r.url()); });
  page.on('dialog', (dialog) => dialog.accept());
  const time = '2026-10-30T00:00:00Z', empty = { items: [], next_cursor: null, total_estimate: null };
  const source = { id: '10000000-0000-4000-8000-000000000001', provider: 'affine', name: '团队知识库', base_url: 'https://docs.example.com', tested_version: '0.27.4', bookmark_count: 1, version: 1, created_at: time, updated_at: time };
  const project = { id: '20000000-0000-4000-8000-000000000001', integration_id: '20000000-0000-4000-8000-000000000002', external_id: 'vikunja:project:1', title: '个人工作台', archived: false, source_url: 'https://tasks.example.com/projects/1', source_updated_at: time, synced_at: time };
  const bookmark = { id: '10000000-0000-4000-8000-000000000002', source_id: source.id, provider: 'affine', resource_type: 'affine_doc', title: '工作台发布说明', note: '记录发布步骤、回滚方式和版本核验结果。', url: 'https://docs.example.com/workspace/team/release', link_kind: 'workspace_doc', affine_workspace_id: 'team', doc_id: 'release', visibility: 'workspace', owner_id: 'user-a', links: [{ resource_id: project.id, resource_type: 'project', title: project.title, source_url: project.source_url }], version: 1, created_at: time, updated_at: time };
  let bookmarks = [bookmark, { ...bookmark, id: '10000000-0000-4000-8000-000000000004', title: '接口设计与约定', note: '前后端共享的接口说明，仅保存手工元数据。', doc_id: 'api', url: 'https://docs.example.com/workspace/team/api', visibility: 'private', links: [] }];
  const event = { id: '10000000-0000-4000-8000-000000000003', title: '版本发布评审', description: '核对验收清单与发布窗口。', location: '线上会议', all_day: false, start_at: '2026-10-30T01:30:00Z', end_at: '2026-10-30T02:30:00Z', start_date: null, end_date: null, timezone: 'Asia/Shanghai', owner_id: 'user-a', version: 1, created_at: time, updated_at: time };
  let events = [event, { ...event, id: '10000000-0000-4000-8000-000000000005', title: '下阶段规划', start_at: new Date(Date.now() + 86400000).toISOString(), end_at: new Date(Date.now() + 90000000).toISOString() }];
  const dashboard = { id: '30000000-0000-4000-8000-000000000001', title: '首页', owner_id: 'user-a', version: 1, is_default: true, created_at: time, updated_at: time };
  const widgets = ['recent_docs', 'calendar_agenda'].map((widget_type, index) => ({ id: `40000000-0000-4000-8000-00000000000${index + 1}`, dashboard_id: dashboard.id, widget_type, config: widget_type === 'recent_docs' ? { limit: 5 } : { days: 7, limit: 5 }, layout: { desktop: { x: index * 6, y: 0, w: 6, h: 5 }, mobile: { y: index * 5, h: 5 } }, version: 1, created_at: time, updated_at: time }));
  await page.route('**/api/v1/**', async (route) => {
    try {
      const request = route.request(), url = new URL(request.url()), pathname = url.pathname, method = request.method();
      const writing = !['GET', 'HEAD'].includes(method); let body, status = 200;
      if (!['/api/v1/setup/status', '/api/v1/me'].includes(pathname)) assert.equal(request.headers()['x-workspace-id'], 'workspace-a');
      if (writing) { assert.equal(request.headers()['x-csrf-token'], 'browser-csrf'); writes.push({ path: pathname, method, body: request.postDataJSON() }); }
      if (pathname === '/api/v1/setup/status') body = { setup_required: false };
      else if (pathname === '/api/v1/me') body = { user: { id: 'user-a', display_name: '演示用户', email: 'test@example.com', created_at: time }, workspaces: [{ id: 'workspace-a', name: '个人工作空间', role: 'owner', created_at: time, updated_at: time }] };
      else if (pathname === '/api/v1/projects') body = { ...empty, items: [project], sync: { status: 'ok', as_of: time, reason: null } };
      else if (pathname === '/api/v1/tasks') body = { ...empty, sync: { status: 'ok', as_of: time, reason: null } };
      else if (pathname === '/api/v1/knowledge/sources') body = { ...empty, items: [{ ...source, bookmark_count: bookmarks.length }] };
      else if (pathname === '/api/v1/knowledge/resources') body = { ...empty, items: bookmarks.filter((item) => !url.searchParams.has('linked_resource_id') || item.links.some((link) => link.resource_id === url.searchParams.get('linked_resource_id'))).slice(0, Number(url.searchParams.get('limit') || 30)) };
      else if (pathname === '/api/v1/knowledge/bookmarks') {
        const input = request.postDataJSON(); assert.equal(input.visibility, 'workspace'); assert.deepEqual(input.linked_resource_ids, [project.id]);
        body = { ...bookmark, ...input, id: '50000000-0000-4000-8000-000000000001', links: bookmark.links }; bookmarks.push(body); status = 201;
      } else if (pathname.startsWith('/api/v1/knowledge/bookmarks/')) {
        const item = bookmarks.find((entry) => entry.id === pathname.split('/').at(-1)); assert(item);
        if (method === 'PATCH') { const input = request.postDataJSON(); assert.equal(input.version, item.version); Object.assign(item, input, { version: item.version + 1 }); body = item; }
        else { assert.equal(Number(url.searchParams.get('version')), item.version); bookmarks = bookmarks.filter((entry) => entry !== item); status = 204; }
      } else if (pathname === '/api/v1/calendar/events' && method === 'GET') {
        const start = Date.parse(url.searchParams.get('start')), end = Date.parse(url.searchParams.get('end')); assert(end > start && end - start <= 92 * 86400000);
        body = { ...empty, items: events.filter((entry) => Date.parse(entry.start_at) < end && Date.parse(entry.end_at) > start).slice(0, Number(url.searchParams.get('limit') || 30)) };
      } else if (pathname === '/api/v1/calendar/events' && method === 'POST') {
        const input = request.postDataJSON(); assert.equal(input.timezone, 'Asia/Shanghai');
        body = { ...event, ...input, id: '60000000-0000-4000-8000-000000000001' };
        if (input.all_day) { assert(!('start_at' in input)); body.start_at = new Date(`${input.start_date}T00:00:00+08:00`).toISOString(); body.end_at = new Date(`${input.end_date}T00:00:00+08:00`).toISOString(); }
        else { assert(!('start_date' in input)); assert.equal(input.start_at, '2026-10-30T01:30:00.000Z'); }
        events.push(body); status = 201;
      } else if (pathname.startsWith('/api/v1/calendar/events/')) {
        const item = events.find((entry) => entry.id === pathname.split('/').at(-1)); assert(item);
        if (method === 'PATCH') { const input = request.postDataJSON(); assert.equal(input.version, item.version); Object.assign(item, input, { version: item.version + 1 }); body = item; }
        else { assert.equal(Number(url.searchParams.get('version')), item.version); events = events.filter((entry) => entry !== item); status = 204; }
      } else if (pathname === '/api/v1/dashboard/overview') body = { dashboard, widgets, summary: { pending_tasks: 0, active_projects: 1, unread_notifications: 0 }, modules: { knowledge: { status: 'ok', as_of: time, retryable: false }, calendar: { status: 'ok', as_of: time, retryable: false } }, today_tasks: [], generated_at: time };
      else throw new Error(`Unexpected ${method} ${pathname}`);
      await route.fulfill({ status, contentType: 'application/json', body: status === 204 ? undefined : JSON.stringify(body) });
    } catch (error) { errors.push(error.message); await route.fulfill({ status: 500, body: '{}' }); }
  });
  await context.addCookies([{ name: 'ws_csrf', value: 'browser-csrf', url: baseUrl }]);
  await page.goto(baseUrl + '/knowledge'); await page.getByRole('heading', { name: '工作台发布说明' }).waitFor();
  await page.getByRole('button', { name: '新增收藏', exact: true }).click();
  await page.getByLabel('收藏标题', { exact: true }).fill('浏览器收藏'); await page.getByLabel('文档链接', { exact: true }).fill('https://docs.example.com/workspace/team/browser');
  await page.getByLabel('关联项目与任务', { exact: true }).selectOption(project.id); await page.getByRole('button', { name: '保存收藏', exact: true }).click();
  await page.getByRole('heading', { name: '浏览器收藏', exact: true }).waitFor();
  await page.getByRole('button', { name: '编辑收藏 浏览器收藏', exact: true }).click(); await page.getByLabel('收藏标题', { exact: true }).fill('浏览器收藏更新'); await page.getByRole('button', { name: '保存收藏', exact: true }).click();
  await page.getByRole('heading', { name: '浏览器收藏更新', exact: true }).waitFor();
  await page.screenshot({ path: path.join(screenshots, 'sprint4-knowledge.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 }); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({ path: path.join(screenshots, 'sprint4-knowledge-mobile.png'), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(baseUrl + '/calendar'); await page.getByRole('button', { name: '版本发布评审', exact: true }).waitFor();
  await page.getByLabel('查看日期', { exact: true }).fill('2026-10-30'); await page.getByLabel('显示时区', { exact: true }).fill('Asia/Shanghai'); await page.getByRole('button', { name: '应用日历范围', exact: true }).click();
  await page.getByRole('button', { name: '新建日程', exact: true }).click(); await page.getByLabel('日程标题', { exact: true }).fill('浏览器日程');
  await page.getByLabel('开始时间', { exact: true }).fill('2026-10-30T09:30'); await page.getByLabel('结束时间', { exact: true }).fill('2026-10-30T10:30'); await page.getByRole('button', { name: '保存日程', exact: true }).click();
  await page.getByRole('button', { name: '浏览器日程', exact: true }).waitFor();
  await page.getByRole('button', { name: '浏览器日程', exact: true }).click(); await page.getByLabel('日程标题', { exact: true }).fill('浏览器日程更新'); await page.getByRole('button', { name: '保存日程', exact: true }).click();
  await page.getByRole('button', { name: '浏览器日程更新', exact: true }).waitFor();
  await page.getByRole('button', { name: '周视图', exact: true }).click(); await page.getByRole('button', { name: '切换到暗色主题' }).click();
  await page.screenshot({ path: path.join(screenshots, 'sprint4-calendar-dark.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 }); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({ path: path.join(screenshots, 'sprint4-calendar-mobile.png'), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 }); await page.goto(baseUrl + '/'); await page.getByRole('heading', { name: '最近文档', exact: true }).waitFor(); await page.getByText('下阶段规划', { exact: true }).waitFor();
  await page.screenshot({ path: path.join(screenshots, 'sprint4-home-cards.png'), fullPage: true });
  assert.deepEqual(errors, []); assert.deepEqual(external, []); assert.equal(writes.length, 4);
  console.log('PASS: bookmark create/edit/associations, calendar create/edit/timezone, version/CSRF/workspace headers, two dashboard cards, mobile overflow checks, light/dark screenshots; no AFFiNE requests or page errors.');
  await browser.close();
})().catch((error) => { console.error(error); process.exit(1); });
