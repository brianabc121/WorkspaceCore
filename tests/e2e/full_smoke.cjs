// 完整端到端冒烟（S6-05，T-0044）：一条命令确认整个系统的基本可用性，发布前与 CI 共用（make smoke-full）。
// 不替代各 Sprint 的验收（那些检查业务细节）；这里只确认每个模块“能打开、能读、不报错”：
// 1. 登录（真实页面）
// 2. 接口：按 services/api/openapi.json 枚举全部 GET 接口逐个请求，都应 200——新增的接口自动纳入；
//    契约已声明、实现任务还没完成的接口返回 501 NOT_IMPLEMENTED，单独列出，不算失败；
//    运维接口报告的核心服务（API、Worker、PostgreSQL、Redis）都是“正常”
// 3. 页面：按 apps/web/src/app/navigation.ts 逐个打开导航里的页面（再加审计日志），
//    标题正确，没有页面错误、控制台错误和 5xx 响应——新增的页面自动纳入
// 4. 实时连接：SSE 能建立（200 + text/event-stream）
// 5. 写入往返：新建日程 → 日历页显示 → 删除；首页加一个便签组件 → 首页显示 → 删除
// 6. 登出后接口返回 401，页面回到登录
// 没有配置 Vikunja、GitHub、n8n、Prometheus 时，相关页面应显示“未配置”而不是报错，这也在检查范围内。
//
// 账号：设置 SMOKE_EMAIL / SMOKE_PASSWORD 时用这个已有账号（生产环境用专门的冒烟账号，它要是工作空间的 owner 或 admin）；
// 否则用同目录的 seed.py 在 api 容器里新建一个临时用户（本机与 CI）。写入的数据在检查结束时删除。
// 用法（依赖由同目录的 package.json / package-lock.json 锁定为 Playwright 1.63.0）：
//   cd tests/e2e && npm ci && npm run browser:install    # 首次：安装依赖与 Chromium
//   node tests/e2e/full_smoke.cjs [BASE_URL]               # 默认 http://127.0.0.1:${HTTP_PORT:-8080}
const { execFileSync } = require('node:child_process')
const { randomBytes } = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require('playwright')

const ROOT = path.resolve(__dirname, '../..')
const base = (process.argv[2] || `http://127.0.0.1:${process.env.HTTP_PORT || '8080'}`).replace(/\/$/, '')
const run = randomBytes(4).toString('hex')

function check(condition, message) { if (!condition) throw new Error(message) }

// 导航里的页面：直接读前端的导航定义，新增页面自动纳入
function pages() {
  const source = fs.readFileSync(path.join(ROOT, 'apps/web/src/app/navigation.ts'), 'utf8')
  const found = [...source.matchAll(/path: '([^']+)', title: '([^']+)'/g)].map(([, route, title]) => ({ route, title }))
  check(found.length >= 12, `导航定义里只找到 ${found.length} 个页面，navigation.ts 的格式可能变了`)
  return [...found, { route: '/settings/audit', title: '审计日志' }]
}

// 全部 GET 接口：路径参数用本次的工作空间与仪表盘，必填的查询参数给固定值；SSE 单独检查
function readOperations(workspace, dashboard) {
  const spec = JSON.parse(fs.readFileSync(path.join(ROOT, 'services/api/openapi.json'), 'utf8'))
  const query = {
    start: '2026-01-05T00:00:00+08:00', end: '2026-01-12T00:00:00+08:00', q: 'smoke',
  }
  const known = { workspace_id: workspace, dashboard_id: dashboard }
  const operations = []
  const skipped = []
  for (const [route, methods] of Object.entries(spec.paths)) {
    const op = methods.get
    if (!op || route === '/api/v1/events/stream') continue
    const params = op.parameters || []
    const unknown = params.filter((p) => p.in === 'path' && !(p.name in known))
    if (unknown.length) { skipped.push(route); continue }  // 需要具体资源 ID（任务、自动化）：由各 Sprint 验收覆盖
    let url = route.replace(/\{([a-z_]+)\}/g, (_, name) => known[name])
    const search = params.filter((p) => p.in === 'query' && p.required).map((p) => `${p.name}=${encodeURIComponent(query[p.name])}`)
    if (search.length) url += `?${search.join('&')}`
    operations.push(url)
  }
  return { operations, skipped }
}

function seedUser(email, password) {
  return execFileSync('python3', [path.join(__dirname, 'seed.py'), 'user', email, '冒烟'], {
    encoding: 'utf8', env: { ...process.env, E2E_PASSWORD: password },
  }).trim().split(/\s+/)
}

// 页面里直接调用接口（带会话 Cookie、CSRF 与工作空间）
async function api(page, workspace, method, pathname, body) {
  return page.evaluate(async ({ method, pathname, body, workspace }) => {
    const csrf = document.cookie.split('; ').find((item) => item.startsWith('ws_csrf='))?.slice(8)
    const headers = { ...(workspace ? { 'X-Workspace-Id': workspace } : {}), ...(csrf ? { 'X-CSRF-Token': decodeURIComponent(csrf) } : {}) }
    if (body !== undefined) headers['Content-Type'] = 'application/json'
    const response = await fetch(pathname, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) })
    const text = await response.text()
    let json = null
    try { json = text ? JSON.parse(text) : null } catch { /* 不是 JSON（例如 /healthz 之外的纯文本） */ }
    return { status: response.status, type: response.headers.get('content-type') || '', body: json }
  }, { method, pathname, body, workspace })
}

;(async () => {
  const started = Date.now()
  let email = process.env.SMOKE_EMAIL
  let password = process.env.SMOKE_PASSWORD
  if (!email || !password) {
    email = `smoke-${run}@smoke.example.com`
    password = `smoke pass ${randomBytes(8).toString('hex')}`
    seedUser(email, password)
  }
  const browser = await chromium.launch()
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const page = await context.newPage()
  const problems = []
  const progress = { last: '登录' }
  // 探测读接口时，契约已声明、尚未实现的接口返回 501：错误码由探测本身检查（必须是 NOT_IMPLEMENTED），
  // 下面的监听在页面检查开始之前不重复记这些 URL；页面检查阶段的任何 501 照常算失败
  const probed = { urls: new Set(), active: true }
  const probedNotImplemented = (status, url) => status === 501 && probed.active && probed.urls.has(url)
  const step = (text) => { progress.last = text.split('：')[0]; console.log(`通过  ${text}`) }
  page.on('pageerror', (error) => problems.push(`页面错误：${error.message}（${progress.last}）`))
  page.on('console', (message) => {
    // 登录前与登出后查询当前用户的 401 是预期响应，浏览器会把它记为资源加载失败
    if (message.type() !== 'error' || /\b401\b/.test(message.text())) return
    if (/\b501\b/.test(message.text()) && probedNotImplemented(501, message.location().url)) return
    problems.push(`控制台错误：${message.text()}（${progress.last}）`)
  })
  page.on('response', (response) => {
    if (!response.url().startsWith(base) || response.status() < 500) return
    if (probedNotImplemented(response.status(), response.url())) return
    problems.push(`${response.status()} ${response.url()}（${progress.last}）`)
  })
  const cleanup = []
  try {
    // ---------------------------------------------------------------- 1. 登录
    await page.goto(`${base}/login`)
    await page.getByLabel('邮箱').fill(email)
    await page.getByLabel('密码').fill(password)
    await page.getByRole('button', { name: '登录' }).click()
    await page.waitForURL(`${base}/`)
    const me = await api(page, null, 'GET', '/api/v1/me')
    check(me.status === 200, `登录后 /api/v1/me 应为 200，实际 ${me.status}`)
    const workspace = me.body.workspaces?.[0]?.id
    check(workspace, '当前用户没有工作空间')
    const dashboards = await api(page, workspace, 'GET', '/api/v1/dashboards')
    const dashboard = dashboards.body?.items?.find((d) => d.is_default)?.id || dashboards.body?.items?.[0]?.id
    check(dashboard, '工作空间没有仪表盘')
    step(`登录：${email}`)

    // ---------------------------------------------------------------- 2. 全部读接口
    const { operations, skipped } = readOperations(workspace, dashboard)
    for (const url of operations) probed.urls.add(new URL(url, base).href)
    const failed = []
    const declared = []  // 契约已声明、实现任务还没完成的接口（501 NOT_IMPLEMENTED）：单独列出，不算失败
    for (const url of operations) {
      const response = await api(page, workspace, 'GET', url)
      if (response.status === 501 && response.body?.error?.code === 'NOT_IMPLEMENTED') declared.push(url)
      else if (response.status !== 200) failed.push(`${url} → ${response.status} ${response.body?.error?.code || ''}`)
    }
    check(failed.length === 0, `读接口没有全部返回 200：\n  ${failed.join('\n  ')}`)
    const implemented = operations.length - declared.length
    check(implemented >= 25, `只找到 ${implemented} 个已实现的读接口，openapi.json 可能不完整`)
    step(`全部读接口返回 200：${implemented} 个（需要具体资源 ID 的 ${skipped.length} 个由各 Sprint 验收覆盖）`)
    if (declared.length) console.log(`说明  契约已声明、尚未实现（501）：${declared.length} 个\n  ${declared.join('\n  ')}`)

    // ---------------------------------------------------------------- 核心服务都正常（Worker 心跳每分钟一次，刚启动时最多等 90 秒）
    let core = []
    for (const deadline = Date.now() + 90000; ;) {
      const services = await api(page, workspace, 'GET', '/api/v1/operations/services')
      core = (services.body?.items || []).filter((s) => s.kind === 'core')
      if ((core.length >= 4 && core.every((s) => s.status === 'ok')) || Date.now() > deadline) break
      await page.waitForTimeout(5000)  // 检查结果在 api 进程里缓存 15 秒
    }
    const notOk = core.filter((s) => s.status !== 'ok').map((s) => `${s.name} ${s.status}`)
    check(core.length >= 4 && notOk.length === 0, `核心服务没有全部正常：${notOk.join('、') || `只有 ${core.length} 个`}`)
    step(`核心服务正常：${core.map((s) => s.name).join('、')}`)

    // ---------------------------------------------------------------- 3. 全部页面
    probed.active = false
    for (const { route, title } of pages()) {
      progress.last = `打开 ${route}`
      await page.goto(`${base}${route}`)
      await page.getByRole('heading', { name: title, level: 1, exact: true }).waitFor({ timeout: 15000 })
      // SSE 是长连接，不能等 networkidle：给页面的查询留出时间，期间的错误由上面的监听收集
      await page.waitForTimeout(1500)
      check(!(await page.getByRole('heading', { name: /页面未找到|无权访问|暂时无法连接/ }).count()), `${route} 显示了错误页`)
    }
    step(`全部页面能打开、标题正确：${pages().map((p) => p.route).join('、')}`)

    // ---------------------------------------------------------------- 4. 实时连接
    const sse = await page.evaluate(async (workspace) => {
      const controller = new AbortController()
      const timer = setTimeout(() => controller.abort(), 10000)
      try {
        const response = await fetch(`/api/v1/events/stream?workspace_id=${workspace}`, { signal: controller.signal })
        return { status: response.status, type: response.headers.get('content-type') || '' }
      } catch (error) {
        return { status: 0, type: String(error) }
      } finally {
        clearTimeout(timer)
        controller.abort()
      }
    }, workspace)
    check(sse.status === 200 && sse.type.startsWith('text/event-stream'), `SSE 应能建立：${sse.status} ${sse.type}`)
    step('实时连接（SSE）能建立')

    // ---------------------------------------------------------------- 5. 写入往返
    const title = `冒烟日程 ${run}`
    const event = await api(page, workspace, 'POST', '/api/v1/calendar/events', {
      title, timezone: 'Asia/Shanghai', start_at: '2026-01-07T10:00:00+08:00', end_at: '2026-01-07T11:00:00+08:00',
    })
    check(event.status === 201, `新建日程应为 201，实际 ${event.status}`)
    cleanup.push(() => api(page, workspace, 'DELETE', `/api/v1/calendar/events/${event.body.id}?version=${event.body.version}`))
    const listed = await api(page, workspace, 'GET', '/api/v1/calendar/events?start=2026-01-07T00:00:00%2B08:00&end=2026-01-08T00:00:00%2B08:00')
    check(listed.body?.items?.some((e) => e.id === event.body.id), '新建的日程不在列表里')
    const note = `冒烟便签 ${run}`
    const widget = await api(page, workspace, 'POST', `/api/v1/dashboards/${dashboard}/widgets`, {
      widget_type: 'note', config: { text: note },
      layout: { desktop: { x: 0, y: 200, w: 4, h: 3 }, mobile: { y: 200, h: 3 } },
    })
    check(widget.status === 201, `添加便签组件应为 201，实际 ${widget.status}`)
    cleanup.push(() => api(page, workspace, 'DELETE', `/api/v1/widgets/${widget.body.id}?version=${widget.body.version}`))
    progress.last = '首页显示新加的便签'
    await page.goto(`${base}/`)
    await page.getByText(note).first().waitFor({ timeout: 15000 })
    while (cleanup.length) {
      const result = await cleanup.pop()()
      check(result.status === 204, `删除测试数据应为 204，实际 ${result.status}`)
    }
    const after = await api(page, workspace, 'GET', '/api/v1/calendar/events?start=2026-01-07T00:00:00%2B08:00&end=2026-01-08T00:00:00%2B08:00')
    check(!after.body?.items?.some((e) => e.id === event.body.id), '删除后日程仍在列表里')
    step('写入往返：新建日程与首页便签 → 列表与首页可见 → 删除')

    // ---------------------------------------------------------------- 6. 登出
    progress.last = '登出'
    await page.getByRole('button', { name: '登出' }).click()
    await page.waitForURL(/\/login/)
    const out = await api(page, null, 'GET', '/api/v1/me')
    check(out.status === 401, `登出后 /api/v1/me 应为 401，实际 ${out.status}`)
    step('登出：回到登录页，接口返回 401')

    check(problems.length === 0, `检查过程中出现错误：\n  ${problems.join('\n  ')}`)
    console.log(`\n完整冒烟全部通过（${((Date.now() - started) / 1000).toFixed(1)} 秒）`)
  } catch (error) {
    console.error(`失败  ${error.message}`)
    if (problems.length) console.error(`  期间的错误：\n  ${problems.join('\n  ')}`)
    for (const undo of cleanup.reverse()) await undo().catch(() => {})
    process.exitCode = 1
  } finally {
    await browser.close()
  }
})()
