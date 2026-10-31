// Local synthetic BFF smoke; no monitoring server or Docker access.
const { chromium } = require('../../../tests/e2e/node_modules/playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const baseUrl = process.env.WEB_BASE_URL || 'http://127.0.0.1:5196';
const shots = path.resolve(__dirname, '../docs/screenshots');
(async () => {
  fs.mkdirSync(shots, { recursive: true });
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined, headless: true, args: ['--no-sandbox'] });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, timezoneId: 'Asia/Shanghai' });
  const page = await context.newPage(), errors = [], external = [], reads = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('request', (request) => { if (new URL(request.url()).origin !== baseUrl) external.push(request.url()); });
  const time = '2026-10-31T03:00:00Z', empty = { items: [], next_cursor: null, total_estimate: null };
  let state = 'ok', role = 'owner';
  const serviceData = { generated_at: time, items: [
    { id: 'api', name: 'API', kind: 'core', status: 'ok', reason: null, checked_at: time, latency_ms: 0 },
    { id: 'worker', name: 'Worker', kind: 'core', status: 'degraded', reason: 'heartbeat_overdue', checked_at: time, latency_ms: null },
    { id: 'postgres', name: 'PostgreSQL', kind: 'core', status: 'down', reason: 'timeout', checked_at: time },
    { id: 'redis', name: 'Redis', kind: 'core', status: 'unknown', reason: 'not_checked', checked_at: null },
    { id: 'prometheus', name: 'Prometheus', kind: 'monitoring', status: 'not_configured', reason: 'not_deployed', checked_at: null },
    { id: 'github', name: '团队 GitHub', kind: 'integration', status: 'degraded', reason: 'rate_limited', checked_at: time },
  ] };
  const titles = { request_rate: '请求速率', latency_p50: '延迟 P50', latency_p95: '延迟 P95', latency_p99: '延迟 P99', error_rate: '5xx 占比', services_up: '服务在线' };
  const points = [0.2, 0.3, null, 0, 0.1].map((value, index) => ({ at: new Date(Date.parse(time) - (4 - index) * 60000).toISOString(), value }));
  const dashboard = { id: '10000000-0000-4000-8000-000000000038', title: '首页', owner_id: 'user-a', version: 1, is_default: true, created_at: time, updated_at: time };
  const widgets = ['service_health', 'api_latency'].map((widget_type, index) => ({ id: '20000000-0000-4000-8000-00000000003' + index, dashboard_id: dashboard.id, widget_type, config: index ? { range: '6h', quantile: 'p99' } : { limit: 8 }, layout: { desktop: { x: index * 6, y: 0, w: 6, h: index ? 8 : 12 }, mobile: { y: index * 12, h: index ? 8 : 12 } }, version: 1, created_at: time, updated_at: time }));
  await page.route('**/api/v1/**', async (route) => {
    try {
      const request = route.request(), url = new URL(request.url()), p = url.pathname; let body;
      assert.equal(request.method(), 'GET');
      if (!['/api/v1/me', '/api/v1/setup/status'].includes(p)) assert.equal(request.headers()['x-workspace-id'], 'workspace-a');
      if (p === '/api/v1/setup/status') body = { setup_required: false };
      else if (p === '/api/v1/me') body = { user: { id: 'user-a', display_name: '演示用户', email: 'test@example.com', created_at: time }, workspaces: [{ id: 'workspace-a', name: '个人工作空间', role, created_at: time, updated_at: time }] };
      else if (p === '/api/v1/operations/services') body = serviceData;
      else if (p === '/api/v1/operations/metrics') {
        assert([...url.searchParams.keys()].every((key) => ['range', 'views'].includes(key))); reads.push(url.search);
        const ids = url.searchParams.getAll('views');
        body = { range: url.searchParams.get('range') || '1h', step_seconds: 60, grafana_url: state === 'not_configured' ? null : 'https://dash.example.com/d/workspace', sync: { status: state, as_of: state === 'stale' ? '2026-10-31T02:00:00Z' : state === 'ok' ? time : null }, views: ['not_configured', 'unavailable'].includes(state) ? [] : (ids.length ? ids : ['request_rate', 'latency_p95']).map((id) => ({ id, title: titles[id], unit: id.startsWith('latency') ? 'seconds' : 'requests_per_second', series: [{ label: '', points }] })) };
      } else if (p === '/api/v1/dashboard/overview') body = { dashboard, widgets, summary: { pending_tasks: 0, active_projects: 0, unread_notifications: 0 }, modules: { operations: { status: 'unavailable', reason: 'service_down', as_of: time, retryable: true } }, today_tasks: [], generated_at: time };
      else if (p === '/api/v1/search') body = empty;
      else throw new Error('Unexpected ' + p);
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    } catch (error) { errors.push(error.message); await route.fulfill({ status: 500, body: '{}' }); }
  });
  await page.goto(baseUrl + '/operations'); await page.getByRole('region', { name: 'API', exact: true }).waitFor();
  await page.getByRole('region', { name: 'Prometheus', exact: true }).getByText('未部署 / 未配置', { exact: true }).waitFor();
  const graph = page.getByRole('region', { name: '延迟 P95', exact: true }); await graph.waitFor(); assert.equal(await graph.locator('svg polyline').count(), 2); await graph.getByText('无数据', { exact: true }).waitFor(); await graph.getByText('0 s', { exact: true }).waitFor();
  const grafana = page.getByRole('link', { name: /在 Grafana 中查看/ }); assert.equal(await grafana.getAttribute('target'), '_blank'); assert.equal(await grafana.getAttribute('rel'), 'noopener noreferrer'); assert.equal(await page.locator('iframe').count(), 0);
  await page.screenshot({ path: path.join(shots, 'sprint5-operations.png'), fullPage: true });
  await page.getByLabel('查看时间范围', { exact: true }).selectOption('6h'); await page.getByLabel('预设指标视图', { exact: true }).selectOption('latency_p99'); await page.getByRole('region', { name: '延迟 P99', exact: true }).waitFor();
  assert(reads.some((query) => new URLSearchParams(query).get('range') === '6h' && new URLSearchParams(query).get('views') === 'latency_p99'));
  state = 'stale'; await page.getByRole('button', { name: '刷新运维数据', exact: true }).click(); await page.getByText(/数据可能过期.*截至/).waitFor();
  state = 'not_configured'; await page.getByRole('button', { name: '刷新运维数据', exact: true }).click(); await page.getByText('未部署监控。', { exact: true }).waitFor(); assert.equal(await page.getByRole('link', { name: /Grafana/ }).count(), 0);
  state = 'unavailable'; await page.getByRole('button', { name: '刷新运维数据', exact: true }).click(); await page.getByText('指标暂不可用，请稍后刷新。', { exact: true }).waitFor(); assert.equal(await page.getByText('暂无指标数据。').count(), 0);
  state = 'ok'; await page.getByRole('button', { name: '刷新运维数据', exact: true }).click(); await page.getByRole('region', { name: '延迟 P99', exact: true }).waitFor();
  await page.getByRole('button', { name: '切换到暗色主题' }).click(); await page.screenshot({ path: path.join(shots, 'sprint5-operations-dark.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 }); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false); await page.screenshot({ path: path.join(shots, 'sprint5-operations-mobile.png'), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 }); await page.goto(baseUrl + '/'); await page.getByRole('article', { name: '服务状态', exact: true }).waitFor(); await page.getByRole('article', { name: 'API 延迟', exact: true }).getByRole('region', { name: '延迟 P99' }).waitFor(); assert.equal(await page.getByText('有核心服务不可用', { exact: true }).count(), 2);
  await page.screenshot({ path: path.join(shots, 'sprint5-operations-home.png'), fullPage: true });
  role = 'viewer'; await page.goto(baseUrl + '/operations'); await page.reload(); await page.getByRole('region', { name: 'API', exact: true }).waitFor(); assert.equal(await page.getByRole('button', { name: /保存|触发/ }).count(), 0);
  assert.deepEqual(errors, []); assert.deepEqual(external, []);
  console.log('PASS: service states/check times, preset range/view queries, null trend gaps vs zero, stale/unavailable/not-configured metrics, safe optional Grafana link, core-down overview with both cards, viewer read-only, dark/light/mobile screenshots; no external requests, iframe or page errors.');
  await browser.close();
})().catch((error) => { console.error(error); process.exit(1); });
