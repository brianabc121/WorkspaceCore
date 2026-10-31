// Sprint 5 浏览器验收（T-0040）：真实后端上的运维、通知与自动化——
// 运维页服务状态与预设指标 → 首页“服务状态”“API 延迟”组件 →
// 通知：SSE 推送后顶栏未读数变化、通知中心纯文本、首页通知组件、标为已读 →
// A16：Caddy 断开、api 重启后 SSE 自动重连并重新拉取 →
// 自动化：配置 n8n 连接（密钥只写不读）→ 登记白名单 → 确认后触发（带 Idempotency-Key）→ 运行记录随对账更新 →
// 结果未知时不自动重发 → 停掉 Prometheus 后显示“数据可能过期” → 移动端 → viewer 只读 →
// DoD：浏览器只访问工作台自己的地址，页面与响应里没有监控内部地址和密钥。
// n8n 用模拟服务（fake_n8n_server.py，与 sprint5_acceptance.py 相同），.env 的 INTEGRATION_ALLOWED_HOSTS 需含 fake-n8n。
//
// 前提：Compose 全栈已启动并迁移（make up && make migrate），.env 里 PROMETHEUS_URL=http://prometheus:9090，
// 并已启动 prometheus（docker compose --profile monitoring up -d prometheus）。
// 脚本中途会停掉再启动 prometheus、caddy，并重启 api。
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
function docker(...args) {
  // docker compose 的进度输出在 stderr：收起来，只在失败时随异常显示
  return execFileSync('docker', args, { cwd: ROOT, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim()
}
// 断开期间浏览器会把 SSE 与接口的连接失败记为控制台错误，这是预期的。
// Chrome 的“Failed to load resource: net::ERR_…”文本里不带地址，地址在 location().url 里，所以两者一起匹配；
// Caddy 停止时，连接还可能报 ERR_SOCKET_NOT_CONNECTED（2026-10-31 main 上出现过，T-0051）
const outage = { active: false }
const OUTAGE_ERRORS = /ERR_CONNECTION|ERR_EMPTY_RESPONSE|ERR_INCOMPLETE_CHUNKED_ENCODING|ERR_SOCKET_NOT_CONNECTED|\b50[23]\b|events\/stream/
function consoleProblem(message) {
  if (message.type() !== 'error') return null
  // 登录前查询当前用户的 401 是预期响应，浏览器会把它记为资源加载失败
  if (/\b401\b/.test(message.text())) return null
  const where = message.location()?.url || ''
  const text = where ? `${message.text()}（${where}）` : message.text()
  if (outage.active && OUTAGE_ERRORS.test(text)) return null
  return text
}
const progress = { last: '登录' }

// 模拟 n8n：在 api 镜像里运行，加入 integrations 网络（别名 fake-n8n），控制接口发布在本机
const FAKE_NAME = 'ws-e2e-fake-n8n'
const FAKE_PORT = 18091
const N8N_KEY = 'n8n_api_e2e_' + randomBytes(16).toString('hex')
const N8N_SECRET = 'e2e-trigger-' + randomBytes(16).toString('hex')
const WORKFLOW = 'wfE2eBrowser01'
const HOOK = `e2e-browser-${run}`
function startFakeN8n() {
  stopFakeN8n()
  const image = docker('inspect', '--format', '{{.Config.Image}}', docker('compose', 'ps', '-q', 'api'))
  docker(
    'run', '-d', '--name', FAKE_NAME, '-p', `127.0.0.1:${FAKE_PORT}:8080`,
    '-e', `FAKE_N8N_API_KEY=${N8N_KEY}`, '-e', `FAKE_N8N_SECRET=${N8N_SECRET}`,
    '-v', `${ROOT}/services/api/tests:/fake/tests:ro`, '-v', `${ROOT}/tests/e2e/fake_n8n_server.py:/fake/server.py:ro`,
    '--entrypoint', 'python', image, '/fake/server.py',
  )
  docker('network', 'connect', '--alias', 'fake-n8n', `${envValue('COMPOSE_PROJECT_NAME') || 'my-workspace'}_integrations`, FAKE_NAME)
}
function stopFakeN8n() {
  try { docker('rm', '-f', FAKE_NAME) } catch { /* 没有在运行 */ }
}
async function fakeN8n(method, pathname, body) {
  const response = await fetch(`http://127.0.0.1:${FAKE_PORT}${pathname}`, {
    method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body),
  })
  check(response.ok, `模拟 n8n ${pathname} 失败：${response.status}`)
  return response.status === 204 ? null : response.json()
}
async function fakeN8nReady() {
  for (let i = 0; i < 60; i += 1) {
    try { await fakeN8n('GET', '/_fake/stats'); return true } catch { await new Promise((r) => setTimeout(r, 500)) }
  }
  return false
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
// 顶栏通知入口的名称形如“通知，3 条未读”
async function unread(page) {
  const label = await page.getByRole('button', { name: /^通知，\d+ 条未读$/ }).getAttribute('aria-label')
  return Number(label.match(/(\d+) 条未读/)[1])
}
function unreadIs(page, count, timeout = 15000) {
  return page.getByRole('button', { name: `通知，${count} 条未读`, exact: true }).waitFor({ timeout })
}

async function login(browser, email, problems, traffic) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const page = await context.newPage()
  page.on('pageerror', (error) => problems.push(error.message))
  page.on('console', (message) => {
    const text = consoleProblem(message)
    if (text) problems.push(`${text}（在“${progress.last}”之后）`)
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
  const [ownerId, workspace, dashboard] = seed('user', `owner5-${run}@browser.example.com`, 'Owner')
  const [viewerId] = seed('user', `viewer5-${run}@browser.example.com`, 'Viewer')
  seed('member', workspace, viewerId, 'viewer')

  const browser = await chromium.launch()
  const problems = []
  const traffic = { requests: [], bodies: [] }
  const step = (text) => { progress.last = text.split('：')[0]; console.log(`通过  ${text}`) }
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

  // ---------------------------------------------------------------- 通知：SSE 推送、通知中心、首页组件（S5-03）
  const notice = (key) => `浏览器通知 ${run} ${key}`
  const added = await api(page, workspace, 'POST', `/api/v1/dashboards/${dashboard}/widgets`, {
    widget_type: 'notifications', config: { limit: 5, unread_only: true },
    layout: { desktop: { x: 0, y: 25, w: 6, h: 5 }, mobile: { y: 40, h: 5 } },
  })
  check(added.status === 201, `添加通知组件失败：${added.status}`)
  await page.goto(`${base}/`)
  const before = await unread(page)
  seed('notify', workspace, ownerId, `${run}-1`, notice(1))
  await unreadIs(page, before + 1, 5000) // 不刷新页面：由 SSE 的失效事件触发重新拉取
  const homeCard = page.getByRole('article', { name: '通知' })
  await homeCard.getByRole('region', { name: '首页通知列表' }).getByText(notice(1)).waitFor()
  await page.getByRole('button', { name: `通知，${before + 1} 条未读`, exact: true }).click()
  const center = page.getByRole('dialog', { name: '通知中心' })
  const listed = center.getByRole('region', { name: '通知列表' })
  await listed.getByRole('link', { name: notice(1) }).waitFor()
  check((await listed.getByRole('link', { name: notice(1) }).getAttribute('href')) === '/operations', '通知链接应是应用内地址')
  check(await listed.getByText('<b>按纯文本显示</b>').count() === 1, '通知正文应按纯文本显示，不解析 HTML')
  await center.getByRole('button', { name: '关闭对话框' }).click()
  await homeCard.getByRole('button', { name: `标为已读：${notice(1)}` }).click()
  await unreadIs(page, before)
  step('通知：写入后不刷新页面，顶栏未读数经 SSE 加一；通知中心按纯文本显示、链接是应用内地址；首页组件标为已读后顶栏同步减一')

  // ---------------------------------------------------------------- A16：SSE 断开后重连并重新拉取
  outage.active = true
  docker('compose', 'stop', 'caddy')
  seed('notify', workspace, ownerId, `${run}-2`, notice(2)) // 断开期间发生的变化
  docker('compose', 'start', 'caddy')
  await unreadIs(page, before + 1, 30000)
  docker('compose', 'restart', 'api') // 重启期间 Caddy 返回 502：EventSource 会被关闭
  docker('compose', 'up', '-d', '--no-build', '--wait', 'api')
  seed('notify', workspace, ownerId, `${run}-3`, notice(3))
  await unreadIs(page, before + 2, 60000)
  outage.active = false
  check(page.url() === `${base}/`, '重连期间页面不应跳转或要求重新登录')
  step('A16：Caddy 断开期间写入的通知在重连后出现；api 重启（期间返回 502）后 SSE 恢复，新通知照常推送，页面没有重新加载')
  await page.getByRole('button', { name: `通知，${before + 2} 条未读`, exact: true }).click()
  await center.getByRole('button', { name: '全部标为已读' }).click()
  await unreadIs(page, 0)
  await center.getByRole('button', { name: '关闭对话框' }).click()
  step('通知中心“全部标为已读”后顶栏未读数为 0')

  // ---------------------------------------------------------------- 自动化：连接、白名单、确认后触发（S5-04）
  startFakeN8n()
  check(await fakeN8nReady(), '模拟 n8n 没有就绪')
  await fakeN8n('POST', '/_fake/control', { add_workflow: { id: WORKFLOW, path: HOOK }, next_status: 'error' })
  await page.goto(`${base}/automations`)
  await page.getByRole('heading', { name: '自动化', level: 1 }).waitFor()
  const connection = page.getByRole('region', { name: 'n8n 连接' })
  await connection.getByText(/尚未配置/).waitFor()
  await connection.getByRole('button', { name: '配置 n8n 连接' }).click()
  const connect = page.getByRole('dialog', { name: '配置 n8n 连接' })
  await connect.getByLabel('服务地址').fill('http://fake-n8n:8080')
  await connect.getByLabel('API Key', { exact: true }).fill(N8N_KEY)
  await connect.getByLabel('触发共享密钥').fill(N8N_SECRET)
  await connect.getByRole('button', { name: '保存连接' }).click()
  await connection.getByText(/API Key 已设置 · 共享密钥 已设置/).waitFor()
  await connection.getByRole('button', { name: '测试 n8n 连接' }).click()
  await page.getByRole('region', { name: '连接检查结果' }).getByText('n8n · 必需检查通过').waitFor({ timeout: 20000 })
  const flowName = `浏览器日报 ${run}`
  await connection.getByRole('button', { name: '登记工作流' }).click()
  const register = page.getByRole('dialog', { name: '登记工作流' })
  await register.getByLabel('名称', { exact: true }).fill(flowName)
  await register.getByLabel('工作流 ID').fill(WORKFLOW)
  await register.getByLabel('触发路径').fill(HOOK)
  await register.getByRole('button', { name: '添加参数' }).click()
  await register.getByLabel('参数标识').nth(0).fill('recipient')
  await register.getByLabel('显示名称').nth(0).fill('收件人')
  await register.getByLabel('必填').nth(0).check()
  await register.getByLabel('敏感值').nth(0).check()
  await register.getByRole('button', { name: '添加参数' }).click()
  await register.getByLabel('参数标识').nth(1).fill('days')
  await register.getByLabel('显示名称').nth(1).fill('天数')
  await register.getByLabel('类型').nth(1).selectOption('number')
  await register.getByRole('button', { name: '保存工作流' }).click()
  const flow = page.getByRole('region', { name: flowName, exact: true })
  await flow.getByText('已激活').waitFor()
  check(!(await page.content()).includes(N8N_KEY) && !(await page.content()).includes(N8N_SECRET), '页面不应出现 n8n 的 API Key 或共享密钥')
  step('自动化页：配置 n8n 连接后只显示“已设置”，测试连接通过；登记白名单工作流（含敏感参数与数字参数），状态为已激活')

  const triggers = []
  page.on('request', (request) => { if (/\/api\/v1\/automations\/[^/]+\/trigger$/.test(request.url())) triggers.push(request) })
  const triggerDialog = page.getByRole('dialog', { name: `触发工作流：${flowName}` })
  await flow.getByRole('button', { name: `触发：${flowName}` }).click()
  await triggerDialog.getByRole('button', { name: '检查并确认' }).click()
  await triggerDialog.getByRole('alert').filter({ hasText: '收件人为必填项。' }).waitFor()
  await triggerDialog.getByLabel('收件人 *').fill('boss@example.com')
  await triggerDialog.getByLabel('天数').fill('7')
  await triggerDialog.getByRole('button', { name: '检查并确认' }).click()
  await triggerDialog.getByText('收件人：已填写（敏感值隐藏）').waitFor()
  check(triggers.length === 0, '确认之前不应提交触发')
  await triggerDialog.getByRole('button', { name: '确认触发' }).click()
  await triggerDialog.getByText('已受理，等待执行。').waitFor()
  check(triggers.length === 1 && /^[0-9a-f-]{36}$/.test(triggers[0].headers()['idempotency-key'] || ''), '触发应只提交一次，并带 UUID 形式的 Idempotency-Key')
  let stats = await fakeN8n('GET', '/_fake/stats')
  check(stats.triggers === 1 && JSON.stringify(stats.bodies[0].params) === JSON.stringify({ recipient: 'boss@example.com', days: 7 }), `n8n 应只收到一次、只含声明过的参数：${JSON.stringify(stats.bodies)}`)
  await triggerDialog.getByRole('button', { name: '关闭对话框' }).click()
  step('触发：必填参数在前端被拦下；确认页隐藏敏感值；确认后只提交一次，带 Idempotency-Key，n8n 只收到声明过的参数')

  await flow.getByRole('button', { name: '查看运行记录' }).click()
  const runs = flow.getByRole('table', { name: `${flowName}运行记录` })
  await runs.getByText('工作流在节点「发送邮件」失败（WrappedExecutionError）').waitFor({ timeout: 150000 }) // Worker 每分钟第 15 秒对账；页面由 SSE 刷新
  await flow.getByText(/最近执行：失败/).waitFor()
  step('运行记录：Worker 对账后不刷新页面即显示“失败”与遮蔽后的错误摘要（节点名与错误类型）')

  await fakeN8n('POST', '/_fake/control', { next_status: 'success', trigger_failure: 'timeout' })
  await flow.getByRole('button', { name: `触发：${flowName}` }).click()
  await triggerDialog.getByLabel('收件人 *').fill('boss@example.com')
  await triggerDialog.getByRole('button', { name: '检查并确认' }).click()
  await triggerDialog.getByRole('button', { name: '确认触发' }).click()
  await triggerDialog.getByText('结果未知，正在确认', { exact: true }).waitFor({ timeout: 30000 })
  await page.waitForTimeout(5000)
  stats = await fakeN8n('GET', '/_fake/stats')
  check(stats.triggers === 2 && triggers.length === 2, `结果未知时不应自动重发：n8n 收到 ${stats.triggers} 次，页面提交 ${triggers.length} 次`)
  await triggerDialog.getByRole('button', { name: '关闭对话框' }).click()
  await runs.getByText('成功').waitFor({ timeout: 150000 })
  check(await runs.getByText('结果未知，正在确认').count() === 0, '对账后不应再有结果未知的记录')
  step('结果未知：n8n 响应超时后页面显示“结果未知，正在确认”，不自动重发；对账按 traceparent 对上后运行记录变为“成功”')

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
  for (const route of ['/operations', '/automations', '/']) {
    await page.goto(`${base}${route}`)
    await page.locator('h1').first().waitFor()
    check(await noHorizontalScroll(page), `${route} 在 390px 宽度下不应出现横向滚动`)
  }
  await page.setViewportSize({ width: 1440, height: 1000 })
  step('390px 宽度下运维页、自动化页与首页无横向滚动')

  // ---------------------------------------------------------------- viewer：运维可读，通知只看自己的，自动化只读
  const viewer = await login(browser, `viewer5-${run}@browser.example.com`, problems, traffic)
  await viewer.page.getByLabel('当前工作空间').selectOption({ label: 'Owner 的空间' })
  await viewer.page.goto(`${base}/operations`)
  await service(viewer.page, 'PostgreSQL').getByText('正常', { exact: true }).waitFor()
  await viewer.page.getByRole('region', { name: '预设指标' }).getByRole('region', { name: '请求速率', exact: true }).waitFor()
  await viewer.page.goto(`${base}/`)
  await viewer.page.getByRole('article', { name: '服务状态' }).getByRole('region', { name: 'PostgreSQL', exact: true }).waitFor()
  await viewer.page.getByRole('button', { name: '通知，0 条未读', exact: true }).click()
  await viewer.page.getByRole('dialog', { name: '通知中心' }).getByText('暂无通知。').waitFor()
  await viewer.page.getByRole('dialog', { name: '通知中心' }).getByRole('button', { name: '关闭对话框' }).click()
  await viewer.page.goto(`${base}/automations`)
  const viewerFlow = viewer.page.getByRole('region', { name: flowName, exact: true })
  await viewerFlow.getByText(/最近执行：成功/).waitFor()
  check(await viewer.page.getByRole('button', { name: `触发：${flowName}` }).count() === 0, 'viewer 不应看到触发按钮')
  check(await viewer.page.getByRole('region', { name: 'n8n 连接' }).count() === 0, 'viewer 不应看到 n8n 连接设置')
  check(await viewer.page.getByRole('button', { name: '编辑工作流' }).count() === 0 && await viewer.page.getByRole('button', { name: '移出白名单' }).count() === 0, 'viewer 不应看到编辑与移出白名单')
  await viewerFlow.getByRole('button', { name: '查看运行记录' }).click()
  await viewerFlow.getByRole('table', { name: `${flowName}运行记录` }).getByText('工作流在节点「发送邮件」失败（WrappedExecutionError）').waitFor()
  step('viewer：能看运维页与首页运维组件；通知中心只有自己的（owner 的通知不可见）；自动化页能看白名单与运行记录，没有触发、连接设置、编辑与移出入口')

  // ---------------------------------------------------------------- DoD：浏览器拿不到监控内部地址与密钥
  const outside = traffic.requests.filter((url) => !url.startsWith(base) && !url.startsWith('data:') && !url.startsWith('blob:'))
  check(outside.length === 0, `浏览器只应访问工作台自己的地址：${outside.slice(0, 5).join(', ')}`)
  const secrets = [...['WORKSPACE_SECRET_KEY', 'POSTGRES_PASSWORD', 'GRAFANA_ADMIN_PASSWORD'].map(envValue), N8N_KEY, N8N_SECRET].filter((value) => value && value.length >= 8)
  const internal = ['prometheus:9090', 'http://prometheus', 'grafana:3000', 'http://api:', 'redis://', 'postgresql']
  const leaked = traffic.bodies.filter((body) => internal.some((needle) => body.includes(needle)) || secrets.some((secret) => body.includes(secret)))
  check(leaked.length === 0, `接口响应里不应有内部地址或密钥：${leaked[0]?.slice(0, 200)}`)
  const html = await page.content()
  check(!internal.some((needle) => html.includes(needle)) && !secrets.some((secret) => html.includes(secret)), '页面里不应有内部地址或密钥')
  check(traffic.requests.every((url) => !/\/api\/v1\/(query|query_range|admin|status\/)/.test(new URL(url).pathname) && !/:9090\//.test(url)), '浏览器不应请求 Prometheus 接口')
  step(`DoD：浏览器的 ${traffic.requests.length} 个请求都指向工作台自己的地址，${traffic.bodies.length} 个接口响应与页面里没有监控内部地址、n8n 的 API Key 与共享密钥`)

  // 收尾：停用本次的 n8n 连接（模拟服务随脚本结束而删除，理由同 sprint5_acceptance.py）
  const current = await api(page, workspace, 'GET', '/api/v1/automations/connection')
  const disabled = await api(page, workspace, 'PUT', '/api/v1/automations/connection', { version: current.body.version, enabled: false })
  check(disabled.status === 200, `停用 n8n 连接失败：${disabled.status}`)

  await browser.close()
  if (problems.length) throw new Error(`页面错误：${problems.join('; ')}`)
  console.log('\n全部通过（页面无错误）')
})().catch((error) => {
  console.error(`失败  ${error.message}`)
  process.exitCode = 1
}).finally(() => {
  // 中途失败时也把停掉的服务拉起来，后面的步骤还要用
  try { prometheus('start') } catch { /* 已在运行 */ }
  try { docker('compose', 'up', '-d', '--no-build', '--wait', 'api', 'caddy') } catch { /* 已在运行 */ }
  stopFakeN8n()
  process.exit()
})
