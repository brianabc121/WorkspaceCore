// Sprint 5 浏览器验收（T-0040）：真实后端上的运维页与首页运维组件——
// 服务状态与预设指标 → 切换时间范围与视图 → 首页“服务状态”“API 延迟”组件 →
// 停掉 Prometheus 后页面与组件明确显示“数据可能过期”、Prometheus 显示不可用 → 恢复 →
// 移动端 → viewer 可读 → DoD：浏览器只访问工作台自己的地址，页面与响应里没有监控内部地址和密钥。
// 通知中心、SSE 重连（A16）与自动化页等 codex 的 T-0039 合并后补进本文件。
//
// 前提：Compose 全栈已启动并迁移（make up && make migrate），.env 里 PROMETHEUS_URL=http://prometheus:9090，
// 并已启动 prometheus（docker compose --profile monitoring up -d prometheus）。脚本中途会停掉再启动它。
// Grafana 可以不部署：没有 GRAFANA_DASHBOARD_URL 时页面不应出现 Grafana 链接。
// 不要求空库：每次用新的工作台用户；用户由同目录的 seed.py 写入。
// 用法（依赖由同目录的 package.json / package-lock.json 锁定为 Playwright 1.63.0）：
//   cd tests/e2e && npm ci && npm run browser:install    # 首次：安装依赖与 Chromium
//   node sprint5_browser.cjs [BASE_URL]                    # 默认 http://127.0.0.1:${HTTP_PORT:-8080}
// CI（.github/workflows/ci-infra.yml）与本机共用。
const { execFileSync } = require('node:child_process')
const { randomBytes } = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require('playwright')

const ROOT = path.resolve(__dirname, '../..')
const base = (process.argv[2] || `http://127.0.0.1:${process.env.HTTP_PORT || '8080'}`).replace(/\/$/, '')
const password = 'e2e sprint5 browser pass'
const run = randomBytes(4).toString('hex')

function envValue(name) {
  if (process.env[name] !== undefined) return process.env[name]
  const file = path.join(ROOT, '.env')
  if (!fs.existsSync(file)) return undefined
  const line = fs.readFileSync(file, 'utf8').split('\n').find((l) => l.startsWith(`${name}=`))
  return line ? line.slice(name.length + 1).trim() : undefined
}
function seed(...args) {
  return execFileSync('python3', [path.join(__dirname, 'seed.py'), ...args], {
    encoding: 'utf8',
    env: { ...process.env, E2E_PASSWORD: password },
  }).trim().split(/\s+/)
}
function check(condition, message) { if (!condition) throw new Error(message) }
function prometheus(action) {
  execFileSync('docker', ['compose', '--profile', 'monitoring', action, 'prometheus'], { cwd: ROOT, stdio: 'ignore' })
}

// 页面里直接调用接口（带会话 Cookie、CSRF 与工作空间），用于准备数据和核对结果
async function api(page, workspace, method, pathname, body) {
  return page.evaluate(async ({ method, pathname, body, workspace }) => {
    const csrf = document.cookie.split('; ').find((item) => item.startsWith('ws_csrf='))?.slice(8)
    const headers = { 'X-Workspace-Id': workspace, ...(csrf ? { 'X-CSRF-Token': decodeURIComponent(csrf) } : {}) }
    if (body !== undefined) headers['Content-Type'] = 'application/json'
    const response = await fetch(pathname, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) })
    return { status: response.status, body: response.status === 204 ? null : await response.json() }
  }, { method, pathname, body, workspace })
}
// 点刷新按钮，直到 locator 出现：服务检查结果在 api 进程里缓存 15 秒，Worker 心跳每分钟一次
async function refreshUntil(page, button, locator, timeout = 60000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    if (await locator.count()) return true
    const target = page.getByRole('button', { name: button }).first()
    if (await target.isEnabled()) await target.click()
    await page.waitForTimeout(1500)
  }
  return (await locator.count()) > 0
}
function service(page, name) {
  return page.getByRole('region', { name: '服务状态' }).getByRole('region', { name, exact: true })
}

async function login(browser, email, problems, traffic) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const page = await context.newPage()
  page.on('pageerror', (error) => problems.push(error.message))
  page.on('console', (message) => {
    // 登录前查询当前用户的 401 是预期响应，浏览器会把它记为资源加载失败
    if (message.type() === 'error' && !/\b401\b/.test(message.text())) problems.push(message.text())
  })
  page.on('request', (request) => traffic.requests.push(request.url()))
  page.on('response', async (response) => {
    if (!response.url().startsWith(`${base}/api/`)) return
    try { traffic.bodies.push(await response.text()) } catch { /* 页面跳转后响应体已不可读 */ }
  })
  await page.goto(`${base}/login`)
  await page.getByLabel('邮箱').fill(email)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '登录' }).click()
  await page.waitForURL(`${base}/`)
  return { context, page }
}
async function noHorizontalScroll(page) {
  return !(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth))
}

;(async () => {
  check(envValue('PROMETHEUS_URL'), '.env 里需要 PROMETHEUS_URL=http://prometheus:9090，并启动 prometheus')
  const grafana = envValue('GRAFANA_DASHBOARD_URL') || ''
  const [, workspace, dashboard] = seed('user', `owner5-${run}@browser.example.com`, 'Owner')
  const [viewerId] = seed('user', `viewer5-${run}@browser.example.com`, 'Viewer')
  seed('member', workspace, viewerId, 'viewer')

  const browser = await chromium.launch()
  const problems = []
  const traffic = { requests: [], bodies: [] }
  const step = (text) => console.log(`通过  ${text}`)
  const { page } = await login(browser, `owner5-${run}@browser.example.com`, problems, traffic)

  // ---------------------------------------------------------------- 运维页：服务状态（S5-02）
  await page.goto(`${base}/operations`)
  await page.getByRole('heading', { name: '运维', level: 1 }).waitFor()
  for (const name of ['API', 'PostgreSQL', 'Redis', 'Prometheus']) {
    const record = service(page, name)
    check(await refreshUntil(page, '刷新运维数据', record.getByText('正常', { exact: true })), `${name} 应显示“正常”`)
    await record.getByText(/最近检查：/).waitFor()
  }
  check(await refreshUntil(page, '刷新运维数据', service(page, 'Worker').getByText('正常', { exact: true })), 'Worker 心跳到达后应显示“正常”')
  if (!envValue('GRAFANA_URL')) await service(page, 'Grafana').getByText('未部署 / 未配置').waitFor()
  step('运维页服务状态：API、PostgreSQL、Redis、Worker、Prometheus 正常，显示最近检查时间；未部署的组件显示“未部署 / 未配置”')

  // ---------------------------------------------------------------- 预设指标与时间范围（S5-01）
  const metrics = page.getByRole('region', { name: '预设指标' })
  for (const title of ['请求速率', '延迟 P50', '延迟 P95', '延迟 P99', '5xx 占比', '服务在线']) {
    await metrics.getByRole('region', { name: title, exact: true }).waitFor()
  }
  await metrics.getByText(/数据截至/).waitFor()
  const grafanaLinks = await page.getByRole('link', { name: /在 Grafana 中查看/ }).count()
  check(grafana ? grafanaLinks === 1 : grafanaLinks === 0, `Grafana 链接数量不对：${grafanaLinks}（GRAFANA_DASHBOARD_URL=${grafana || '未设置'}）`)
  const queried = []
  page.on('request', (request) => { if (request.url().includes('/api/v1/operations/metrics')) queried.push(request.url()) })
  await page.getByLabel('查看时间范围').selectOption('6h')
  await page.getByLabel('预设指标视图').selectOption('latency_p95')
  await metrics.getByRole('region', { name: '延迟 P95', exact: true }).waitFor()
  await page.waitForFunction(() => document.querySelectorAll('section.metric-view').length === 1)
  check(queried.some((url) => /range=6h/.test(url) && /views=latency_p95/.test(url)), `应按所选范围与视图请求：${queried.join(', ')}`)
  check(queried.every((url) => !/[?&](query|promql|expr)=/.test(url)), '浏览器不应提交 PromQL')
  await page.getByLabel('预设指标视图').selectOption('')
  await page.getByLabel('查看时间范围').selectOption('1h')
  await metrics.getByRole('region', { name: '服务在线', exact: true }).waitFor()
  step(`预设指标：6 个视图带数据截至时间；切换时间范围与视图只提交预设 ID，不提交 PromQL；Grafana 链接${grafana ? '指向配置的看板' : '未配置时不显示'}`)

  // ---------------------------------------------------------------- 首页“服务状态”“API 延迟”组件
  const widgets = [
    { widget_type: 'service_health', config: { limit: 8 }, layout: { desktop: { x: 0, y: 20, w: 6, h: 5 }, mobile: { y: 30, h: 5 } } },
    { widget_type: 'api_latency', config: { range: '1h', quantile: 'p95' }, layout: { desktop: { x: 6, y: 20, w: 6, h: 5 }, mobile: { y: 35, h: 5 } } },
  ]
  for (const widget of widgets) {
    const created = await api(page, workspace, 'POST', `/api/v1/dashboards/${dashboard}/widgets`, widget)
    check(created.status === 201, `添加组件失败：${created.status} ${JSON.stringify(created.body).slice(0, 200)}`)
  }
  await page.goto(`${base}/`)
  const healthCard = page.getByRole('article', { name: '服务状态' })
  const latencyCard = page.getByRole('article', { name: 'API 延迟' })
  await healthCard.getByRole('region', { name: 'PostgreSQL', exact: true }).getByText('正常', { exact: true }).waitFor()
  await latencyCard.getByText('P95 · 1h').waitFor()
  await latencyCard.getByRole('region', { name: '延迟 P95', exact: true }).waitFor()
  await healthCard.getByRole('link', { name: '查看运维页' }).waitFor()
  step('首页“服务状态”组件显示各服务状态，“API 延迟”组件显示 P95 · 1h 的预设视图')

  // ---------------------------------------------------------------- DoD：Prometheus 不可用时明确显示 stale
  prometheus('stop')
  try {
    await page.goto(`${base}/operations`)
    check(await refreshUntil(page, '刷新运维数据', metrics.getByText(/数据可能过期（截至/)), '停掉 Prometheus 后指标应显示“数据可能过期（截至 …）”')
    await metrics.getByRole('region', { name: '请求速率', exact: true }).waitFor()
    check(await refreshUntil(page, '刷新运维数据', service(page, 'Prometheus').getByText('不可用', { exact: true })), 'Prometheus 应显示“不可用”')
    await service(page, 'PostgreSQL').getByText('正常', { exact: true }).waitFor()
    await page.goto(`${base}/`)
    await latencyCard.getByText(/数据可能过期（截至/).waitFor()
    step('DoD：停掉 Prometheus 后运维页与首页“API 延迟”组件显示“数据可能过期（截至 …）”与缓存的视图，Prometheus 显示“不可用”，其他服务照常')
  } finally {
    prometheus('start')
  }
  await page.goto(`${base}/operations`)
  check(await refreshUntil(page, '刷新运维数据', service(page, 'Prometheus').getByText('正常', { exact: true }), 90000), 'Prometheus 恢复后应显示“正常”')
  check(await refreshUntil(page, '刷新运维数据', metrics.getByText(/数据截至/)), 'Prometheus 恢复后指标应不再标为过期')
  check(await metrics.getByText(/数据可能过期/).count() === 0, 'Prometheus 恢复后不应再提示过期')
  step('Prometheus 恢复后服务状态与指标恢复正常，不再提示过期')

  // ---------------------------------------------------------------- 移动端
  await page.setViewportSize({ width: 390, height: 844 })
  for (const route of ['/operations', '/']) {
    await page.goto(`${base}${route}`)
    await page.locator('h1').first().waitFor()
    check(await noHorizontalScroll(page), `${route} 在 390px 宽度下不应出现横向滚动`)
  }
  await page.setViewportSize({ width: 1440, height: 1000 })
  step('390px 宽度下运维页与首页无横向滚动')

  // ---------------------------------------------------------------- viewer 可读
  const viewer = await login(browser, `viewer5-${run}@browser.example.com`, problems, traffic)
  await viewer.page.getByLabel('当前工作空间').selectOption({ label: 'Owner 的空间' })
  await viewer.page.goto(`${base}/operations`)
  await service(viewer.page, 'PostgreSQL').getByText('正常', { exact: true }).waitFor()
  await viewer.page.getByRole('region', { name: '预设指标' }).getByRole('region', { name: '请求速率', exact: true }).waitFor()
  await viewer.page.goto(`${base}/`)
  await viewer.page.getByRole('article', { name: '服务状态' }).getByRole('region', { name: 'PostgreSQL', exact: true }).waitFor()
  step('viewer：能看运维页与首页运维组件（运维接口对 viewer 只读开放）')

  // ---------------------------------------------------------------- DoD：浏览器拿不到监控内部地址与密钥
  const outside = traffic.requests.filter((url) => !url.startsWith(base) && !url.startsWith('data:') && !url.startsWith('blob:'))
  check(outside.length === 0, `浏览器只应访问工作台自己的地址：${outside.slice(0, 5).join(', ')}`)
  const secrets = ['WORKSPACE_SECRET_KEY', 'POSTGRES_PASSWORD', 'GRAFANA_ADMIN_PASSWORD'].map(envValue).filter((value) => value && value.length >= 8)
  const internal = ['prometheus:9090', 'http://prometheus', 'grafana:3000', 'http://api:', 'redis://', 'postgresql']
  const leaked = traffic.bodies.filter((body) => internal.some((needle) => body.includes(needle)) || secrets.some((secret) => body.includes(secret)))
  check(leaked.length === 0, `接口响应里不应有监控内部地址或密钥：${leaked[0]?.slice(0, 200)}`)
  const html = await page.content()
  check(!internal.some((needle) => html.includes(needle)) && !secrets.some((secret) => html.includes(secret)), '页面里不应有监控内部地址或密钥')
  check(traffic.requests.every((url) => !/\/api\/v1\/(query|query_range|admin|status\/)/.test(new URL(url).pathname) && !/:9090\//.test(url)), '浏览器不应请求 Prometheus 接口')
  step(`DoD：浏览器的 ${traffic.requests.length} 个请求都指向工作台自己的地址，${traffic.bodies.length} 个接口响应与页面里没有监控内部地址和密钥`)

  await browser.close()
  if (problems.length) throw new Error(`页面错误：${problems.join('; ')}`)
  console.log('\n全部通过（页面无错误）')
})().catch((error) => {
  console.error(`失败  ${error.message}`)
  process.exitCode = 1
}).finally(() => {
  // 中途失败时也把 Prometheus 拉起来，后面的步骤还要用
  try { prometheus('start') } catch { /* 已在运行 */ }
  process.exit()
})
