// Isolated browser smoke against contract-shaped synthetic BFF responses.
const { chromium } = require('../../../tests/e2e/node_modules/playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const baseUrl = process.env.WEB_BASE_URL || 'http://127.0.0.1:5195';
const shots = path.resolve(__dirname, '../docs/screenshots');
(async () => {
  fs.mkdirSync(shots, { recursive: true });
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined, headless: true, args: ['--no-sandbox'] });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, timezoneId: 'Asia/Shanghai' });
  const page = await context.newPage(), errors = [], external = [], writes = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('request', (request) => { if (new URL(request.url()).origin !== baseUrl) external.push(request.url()); });
  const time = '2026-10-31T00:00:00Z', empty = { items: [], next_cursor: null, total_estimate: null };
  const syncOk = { status: 'ok', as_of: time, reason: null, retry_at: null };
  let sync = syncOk, noRows = false, integrations = [];
  const integration = { id: '10000000-0000-4000-8000-000000000031', provider: 'github', name: 'GitHub', base_url: 'https://api.github.com', public_url: 'https://github.com', repositories: ['team/workbench', 'team/api'], status: 'active', token_set: true, token_expires_at: null, tested_version: '2026-03-10', api_version: '2026-03-10', instance_version: null, last_tested_at: time, sync: syncOk, webhooks: { mode: 'unavailable', registered_projects: 0, reason: 'not_supported' }, version: 1, created_at: time, updated_at: time };
  const repo = { id: '20000000-0000-4000-8000-000000000031', integration_id: integration.id, external_id: 'github:repo:31', full_name: 'team/workbench', description: '个人工作台与跨资源搜索', private: true, archived: false, default_branch: 'main', access: 'ok', pushed_at: time, source_url: 'https://github.com/team/workbench', source_updated_at: time, synced_at: time };
  const issue = { ...repo, id: '30000000-0000-4000-8000-000000000031', repo_id: repo.id, repo_full_name: repo.full_name, number: 12, title: '发布前检查搜索入口', state: 'open', author: 'developer', labels: ['bug'], assignees: [], comments: 2, created_at: time, closed_at: null, state_reason: null, source_url: repo.source_url + '/issues/12' };
  const pull = { ...issue, id: '40000000-0000-4000-8000-000000000031', title: '增加全局搜索与 GitHub 投影', draft: false, merged: false, merged_at: null, requested_reviewers: ['reviewer'], head_ref: 'feature/search', base_ref: 'main', source_url: repo.source_url + '/pull/13' };
  const run = { ...repo, id: '50000000-0000-4000-8000-000000000031', repo_id: repo.id, repo_full_name: repo.full_name, name: 'Web CI', display_title: '验证发布前搜索功能', status: 'completed', conclusion: 'failure', event: 'pull_request', head_branch: 'feature/search', head_sha: 'abc123abc123', run_number: 31, run_attempt: 1, actor: 'developer', run_started_at: time, failure_summary: { failed_jobs: [{ name: 'web', failed_steps: ['Typecheck', 'Build'] }] }, source_url: repo.source_url + '/actions/runs/31' };
  const dashboard = { id: '60000000-0000-4000-8000-000000000031', title: '首页', owner_id: 'user-a', version: 1, is_default: true, created_at: time, updated_at: time };
  const widget = { id: '70000000-0000-4000-8000-000000000031', dashboard_id: dashboard.id, widget_type: 'github_activity', config: { kind: 'runs', limit: 5 }, layout: { desktop: { x: 0, y: 0, w: 8, h: 6 }, mobile: { y: 0, h: 6 } }, version: 1, created_at: time, updated_at: time };
  const search = [{ id: issue.id, resource_type: 'issue', title: issue.title, provider: 'github', context: repo.full_name, source_url: issue.source_url, updated_at: time, match: 'title' }, { id: '80000000-0000-4000-8000-000000000031', resource_type: 'event', title: '发布与联调日程', provider: 'local', context: '2026-11-08', source_url: null, updated_at: time, match: 'text' }, { id: '90000000-0000-4000-8000-000000000031', resource_type: 'bookmark', title: '发布说明与接口约定', provider: 'affine', context: null, source_url: 'https://docs.example.com/workspace/team/release', updated_at: time, match: 'title' }];
  await page.route('**/api/v1/**', async (route) => {
    try {
      const request = route.request(), url = new URL(request.url()), p = url.pathname, method = request.method(); let body, status = 200;
      if (!['/api/v1/setup/status', '/api/v1/me'].includes(p)) assert.equal(request.headers()['x-workspace-id'], 'workspace-a');
      if (method !== 'GET') { assert.equal(request.headers()['x-csrf-token'], 'browser-csrf'); writes.push({ path: p, body: request.postDataJSON() }); }
      if (p === '/api/v1/setup/status') body = { setup_required: false };
      else if (p === '/api/v1/me') body = { user: { id: 'user-a', display_name: '演示用户', email: 'test@example.com', created_at: time }, workspaces: [{ id: 'workspace-a', name: '个人工作空间', role: 'owner', created_at: time, updated_at: time }] };
      else if (p === '/api/v1/integrations' && method === 'GET') body = { ...empty, items: integrations };
      else if (p === '/api/v1/integrations' && method === 'POST') { const input = request.postDataJSON(); assert.equal(input.provider, 'github'); assert.deepEqual(input.repositories, ['team/workbench', 'team/api']); assert(!('base_url' in input)); integrations = [integration]; body = integration; status = 201; }
      else if (p.endsWith('/test')) body = { ok: false, status: 'unavailable', instance_version: null, api_version: integration.api_version, tested_version: integration.tested_version, compatible: true, checks: [{ name: 'reachable', ok: true }, { name: 'version', ok: true }, { name: 'token', ok: true }, { name: 'webhooks', ok: false, code: 'not_supported' }], warnings: [], repositories: ['team/workbench', 'team/api'].map((full_name) => ({ full_name, ok: false, checks: ['metadata', 'issues', 'pull_requests', 'actions'].map((name) => ({ name, ok: name !== 'actions', code: name === 'actions' ? 'missing_permission' : null })) })) };
      else if (p.startsWith('/api/v1/developer/')) body = { ...empty, sync, items: noRows ? [] : [p.endsWith('/repos') ? repo : p.endsWith('/issues') ? issue : p.endsWith('/pulls') ? pull : run] };
      else if (p === '/api/v1/dashboard/overview') body = { dashboard, widgets: [widget], summary: { pending_tasks: 0, active_projects: 0, unread_notifications: 0 }, modules: { github: { status: 'ok', as_of: time, retryable: false } }, today_tasks: [], generated_at: time };
      else if (p === '/api/v1/search') { assert(url.searchParams.get('q')); const term = url.searchParams.get('q'); const items = term === 'event-next' ? search.filter((item) => item.resource_type === 'event').map((item) => ({ ...item, context: '2026-12-04' })) : term === 'event' ? search.filter((item) => item.resource_type === 'event') : search; body = { ...empty, items }; }
      else if (p === '/api/v1/calendar/events') body = empty;
      else throw new Error('Unexpected ' + method + ' ' + p);
      await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    } catch (error) { errors.push(error.message); await route.fulfill({ status: 500, body: '{}' }); }
  });
  await context.addCookies([{ name: 'ws_csrf', value: 'browser-csrf', url: baseUrl }]);
  await page.goto(baseUrl + '/settings/integrations'); await page.getByRole('button', { name: '新建 GitHub 连接', exact: true }).click();
  await page.getByLabel('GitHub Token', { exact: true }).fill('github_pat_synthetic-browser');
  await page.getByLabel('GitHub 仓库列表', { exact: true }).fill('Team/Workbench\nteam/workbench\nteam/api'); await page.getByRole('button', { name: '保存 GitHub 连接', exact: true }).click();
  await page.getByRole('heading', { name: 'GitHub', exact: true }).waitFor(); assert.equal(await page.locator('input[type=password]').count(), 0);
  await page.getByRole('button', { name: '测试连接', exact: true }).click(); await page.getByRole('region', { name: '连接检查结果' }).waitFor();
  assert.equal(await page.getByRole('region', { name: '连接检查结果' }).getByText(/missing_permission/).count(), 2);
  await page.screenshot({ path: path.join(shots, 'sprint4-github-settings.png'), fullPage: true });
  await page.goto(baseUrl + '/developer'); await page.getByRole('heading', { name: repo.full_name, exact: true }).waitFor();
  await page.getByRole('button', { name: 'Issues', exact: true }).click(); await page.getByRole('heading', { name: issue.title, exact: true }).waitFor();
  await page.getByRole('button', { name: 'Pull requests', exact: true }).click(); await page.getByRole('heading', { name: pull.title, exact: true }).waitFor();
  await page.getByRole('button', { name: 'Actions', exact: true }).click(); await page.getByRole('heading', { name: run.display_title, exact: true }).waitFor(); await page.getByText('web：Typecheck、Build', { exact: true }).waitFor();
  await page.getByRole('button', { name: '切换到暗色主题' }).click(); await page.screenshot({ path: path.join(shots, 'sprint4-developer-dark.png'), fullPage: true });
  sync = { status: 'stale', as_of: time, reason: 'rate_limited', retry_at: '2026-11-01T12:34:00Z' };
  await page.getByRole('button', { name: '刷新 GitHub 数据' }).click(); await page.getByText(/GitHub 限流中.*预计.*恢复/).waitFor();
  noRows = true; await page.getByRole('button', { name: '刷新 GitHub 数据' }).click(); await page.getByRole('heading', { name: run.display_title, exact: true }).waitFor({ state: 'detached' }); assert.equal(await page.getByText('没有符合条件的 GitHub 数据。').count(), 0);
  sync = { status: 'unauthorized', as_of: time, reason: 'token_rejected' }; await page.getByRole('button', { name: '刷新 GitHub 数据' }).click(); await page.getByText(/GitHub 授权已失效.*重新连接/).waitFor();
  sync = syncOk; noRows = false; await page.getByRole('button', { name: '刷新 GitHub 数据' }).click(); await page.getByRole('heading', { name: run.display_title, exact: true }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 }); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false); await page.screenshot({ path: path.join(shots, 'sprint4-developer-mobile.png'), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 }); await page.goto(baseUrl + '/'); await page.getByRole('heading', { name: 'GitHub 动态', exact: true }).waitFor(); await page.getByText(run.display_title, { exact: true }).waitFor(); await page.screenshot({ path: path.join(shots, 'sprint4-github-home.png'), fullPage: true });
  await page.keyboard.press('Control+k'); await page.getByLabel('搜索页面与组件').fill('event'); await page.getByRole('link', { name: /发布与联调日程/ }).waitFor();
  await page.getByLabel('搜索页面与组件').press('ArrowDown'); await page.keyboard.press('Enter'); await page.getByLabel('查看日期').waitFor(); assert.equal(await page.getByLabel('查看日期').inputValue(), '2026-11-08');
  await page.keyboard.press('Control+k'); await page.getByLabel('搜索页面与组件').fill('event-next'); await page.getByRole('link', { name: /发布与联调日程/ }).waitFor(); await page.getByLabel('搜索页面与组件').press('ArrowDown'); await page.keyboard.press('Enter'); await page.waitForURL('**/calendar?date=2026-12-04'); assert.equal(await page.getByLabel('查看日期').inputValue(), '2026-12-04');
  await page.keyboard.press('Meta+k'); await page.getByLabel('搜索页面与组件').fill('release'); await page.getByRole('link', { name: /发布前检查搜索入口/ }).waitFor();
  await page.screenshot({ path: path.join(shots, 'sprint4-global-search.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 }); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false); await page.keyboard.press('Escape');
  assert.deepEqual(errors, []); assert.deepEqual(external, []); assert.equal(writes.length, 2);
  console.log('PASS: GitHub fine-grained setup/dedup, per-repository checks, four developer projections/failures, rate-limit/reconnect, activity card, keyboard BFF search/event navigation, workspace/CSRF headers, light/dark/mobile screenshots; no external requests or page errors.');
  await browser.close();
})().catch((error) => { console.error(error); process.exit(1); });
