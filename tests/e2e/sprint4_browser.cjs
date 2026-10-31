// Sprint 4 浏览器验收（T-0032）：真实后端上的知识入口、日历、GitHub 与全局搜索——
// 配置 AFFiNE 地址 → 新增收藏并关联任务 → 任务抽屉显示相关文档 → 日历新建 / 编辑 →
// 首页“最近文档”“近期日程”显示真实数据 → 连接 GitHub → 开发者页 → 限流提示 →
// 顶栏搜索框命中并跳转 → viewer 只读 → 删除日程与收藏后页面与搜索都不再出现。
// GitHub 用模拟服务（fake_github_server.py，与 sprint4_acceptance.py 相同），.env 需要
// WORKSPACE_GITHUB_API_URL=http://fake-github:8080，INTEGRATION_ALLOWED_HOSTS 含 fake-github。
//
// 前提：Compose 全栈已启动并迁移（make up && make migrate）。
// 不要求空库：每次用新的工作台用户；用户由同目录的 seed.py 写入。
// 被关联的 Vikunja 任务直接写进投影（模拟同步结果，Vikunja 本身在 Sprint 3 已实测）：
// 这个集成没有凭据，Worker 不会同步它。
// AFFiNE 地址是不存在的主机：工作台只保存链接，浏览器和后端都不应请求它（DoD）。
// 用法（依赖由同目录的 package.json / package-lock.json 锁定为 Playwright 1.63.0）：
//   cd tests/e2e && npm ci && npm run browser:install    # 首次：安装依赖与 Chromium
//   node sprint4_browser.cjs [BASE_URL]                    # 默认 http://127.0.0.1:${HTTP_PORT:-8080}
// CI（.github/workflows/ci-infra.yml）与本机共用。
const { execFileSync } = require('node:child_process')
const { randomBytes } = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require('playwright')

const ROOT = path.resolve(__dirname, '../..')
const base = (process.argv[2] || `http://127.0.0.1:${process.env.HTTP_PORT || '8080'}`).replace(/\/$/, '')
const password = 'e2e sprint4 browser pass'
const run = randomBytes(4).toString('hex')
const AFFINE = 'http://affine.e2e.invalid:3010'
const ZONE = 'Asia/Shanghai'
const FAKE_NAME = 'ws-e2e-fake-github'
const FAKE_PORT = 18090
const FAKE_REPO = 'e2e/workspace'

function envValue(name) {
  if (process.env[name]) return process.env[name]
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

function docker(...args) {
  return execFileSync('docker', args, { cwd: ROOT, encoding: 'utf8' }).trim()
}
// 模拟 GitHub：在 api 镜像里运行，加入 integrations 网络（别名 fake-github），控制接口发布在本机
function startFakeGitHub(token) {
  stopFakeGitHub()
  const image = docker('inspect', '--format', '{{.Config.Image}}', docker('compose', 'ps', '-q', 'api'))
  docker(
    'run', '-d', '--name', FAKE_NAME, '-p', `127.0.0.1:${FAKE_PORT}:8080`,
    '-e', `FAKE_GITHUB_TOKEN=${token}`, '-e', `FAKE_GITHUB_REPO=${FAKE_REPO}`,
    '-v', `${ROOT}/services/api/tests:/fake/tests:ro`, '-v', `${ROOT}/tests/e2e/fake_github_server.py:/fake/server.py:ro`,
    '--entrypoint', 'python', image, '/fake/server.py',
  )
  docker('network', 'connect', '--alias', 'fake-github', `${envValue('COMPOSE_PROJECT_NAME') || 'my-workspace'}_integrations`, FAKE_NAME)
}
function stopFakeGitHub() {
  try { docker('rm', '-f', FAKE_NAME) } catch { /* 没有在运行 */ }
}
async function fakeGitHub(method, pathname, body) {
  const response = await fetch(`http://127.0.0.1:${FAKE_PORT}${pathname}`, {
    method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body),
  })
  check(response.ok, `模拟 GitHub ${pathname} 失败：${response.status}`)
  return response.status === 204 ? null : response.json()
}
async function fakeGitHubReady() {
  for (let i = 0; i < 60; i += 1) {
    try { await fakeGitHub('GET', '/_fake/stats'); return true } catch { await new Promise((r) => setTimeout(r, 500)) }
  }
  return false
}
// 点刷新按钮，直到 locator 出现：同步由 Worker 异步完成
async function refreshUntil(page, button, locator, timeout = 30000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    if (await locator.count()) return true
    await page.getByRole('button', { name: button }).click()
    await page.waitForTimeout(800)
  }
  return (await locator.count()) > 0
}
async function openSearch(page, q) {
  await page.getByRole('button', { name: /Ctrl K/ }).click()
  await page.getByLabel('搜索页面与组件').fill(q)
}

// 拼进 SQL 的值只允许 UUID，先校验格式
function uuid(value) {
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value)) throw new Error(`不是 UUID：${value}`)
  return `'${value}'`
}
function sql(query) {
  const user = envValue('POSTGRES_USER') || 'workspace'
  const database = envValue('POSTGRES_DB') || 'workspace'
  const output = execFileSync('docker', ['compose', 'exec', '-T', 'postgres', 'psql', '-U', user, '-d', database, '-tAc', query], { cwd: ROOT, encoding: 'utf8' })
  return output.trim().split('\n')[0] // INSERT … RETURNING 之后 psql 还会输出 INSERT 0 1
}
function vikunjaTask(workspaceId, title) {
  const integration = sql(
    'INSERT INTO workspace.integrations (workspace_id, provider, name, base_url, public_url, status)' +
    ` VALUES (${uuid(workspaceId)}, 'vikunja', 'e2e-vikunja', 'http://vikunja:3456', 'http://vikunja:3456', 'active') RETURNING id`,
  )
  return sql(
    'INSERT INTO workspace.external_resources (workspace_id, integration_id, resource_type, external_id, title, source_url, metadata_json, source_updated_at)' +
    ` VALUES (${uuid(workspaceId)}, ${uuid(integration)}, 'task', '${randomBytes(8).toString('hex')}', '${title}',` +
    ` 'http://vikunja:3456/tasks/1', '{"description": "", "done": false, "version": "1"}'::jsonb, now()) RETURNING id`,
  )
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
async function searchTitles(page, workspace, q) {
  const result = await api(page, workspace, 'GET', `/api/v1/search?q=${encodeURIComponent(q)}`)
  check(result.status === 200, `搜索失败：${result.status}`)
  return result.body.items.map((item) => item.title)
}

async function login(browser, email, problems, requests) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, timezoneId: ZONE })
  const page = await context.newPage()
  page.on('pageerror', (error) => problems.push(error.message))
  page.on('console', (message) => {
    // 登录前查询当前用户的 401 是预期响应，浏览器会把它记为资源加载失败
    if (message.type() === 'error' && !/\b401\b/.test(message.text())) problems.push(message.text())
  })
  page.on('request', (request) => requests.push(request.url()))
  page.on('dialog', (dialog) => dialog.accept())
  await page.goto(`${base}/login`)
  await page.getByLabel('邮箱').fill(email)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '登录' }).click()
  await page.waitForURL(`${base}/`)
  return { context, page }
}
// 提交被前端校验拦下：等表单级提示（role=alert，提交的校验结束后才出现）。
// 字段级错误在输入时就会出现，只等它的话，提交的校验可能还没结束，接着改值就会被这次提交带走。
async function rejectedOnSubmit(page, form, button, text) {
  await page.getByRole('button', { name: button }).click()
  await page.getByRole('form', { name: form }).getByRole('alert').filter({ hasText: text }).waitFor()
}
async function noHorizontalScroll(page) {
  return !(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth))
}

;(async () => {
  const [, workspace, dashboard] = seed('user', `owner4-${run}@browser.example.com`, 'Owner')
  const [viewerId] = seed('user', `viewer4-${run}@browser.example.com`, 'Viewer')
  seed('member', workspace, viewerId, 'viewer')
  const taskTitle = `浏览器关联任务 ${run}`
  vikunjaTask(workspace, taskTitle)
  // 明天（显示时区）上午 10 点：落在首页“近期日程”的 7 天窗口里
  const tomorrow = new Intl.DateTimeFormat('en-CA', { timeZone: ZONE }).format(new Date(Date.now() + 86400000))

  const browser = await chromium.launch()
  const problems = []
  const requests = []
  const step = (text) => console.log(`通过  ${text}`)
  const { page } = await login(browser, `owner4-${run}@browser.example.com`, problems, requests)

  // ---------------------------------------------------------------- 配置 AFFiNE 地址（S4-01，管理员）
  await page.goto(`${base}/knowledge`)
  await page.getByText('尚未配置知识来源').waitFor()
  const sourceBodies = []
  page.on('request', (request) => {
    if (request.url().endsWith('/api/v1/knowledge/sources') && request.method() === 'POST') sourceBodies.push(request.postData() || '')
  })
  await page.getByRole('button', { name: '管理知识来源' }).click()
  await page.getByLabel('知识来源名称').fill('AFFiNE')
  await page.getByLabel('AFFiNE 外部地址').fill('http://169.254.169.254')
  await rejectedOnSubmit(page, '知识来源配置', '保存知识来源', '不能使用链路本地、云元数据或未指定地址。')
  await page.getByLabel('AFFiNE 外部地址').fill(AFFINE)
  await page.getByRole('button', { name: '保存知识来源' }).click()
  await page.getByText('知识来源已保存').waitFor()
  check(sourceBodies.length === 1 && sourceBodies[0].includes(AFFINE), `应只提交一次合法地址：${sourceBodies}`)
  step('配置 AFFiNE 外部地址：元数据地址被拒绝且没有提交（A12），合法地址保存成功')

  // ---------------------------------------------------------------- 新增收藏并关联任务（S4-01）
  const title = `浏览器收藏 ${run}`
  const writes = []
  page.on('request', (request) => {
    if (request.url().endsWith('/api/v1/knowledge/bookmarks') && request.method() === 'POST') writes.push(request.url())
  })
  await page.getByRole('button', { name: '新增收藏' }).click()
  await page.getByLabel('收藏标题').fill(title)
  await page.getByLabel('文档链接').fill('javascript:alert(1)')
  await rejectedOnSubmit(page, '收藏编辑', '保存收藏', '文档链接只支持 HTTP 或 HTTPS。')
  check(writes.length === 0, '不合法的链接不应提交到后端')
  await page.getByLabel('文档链接').fill(`${AFFINE}/workspace/ws1/doc-${run}?mode=page&refreshKey=1#heading`)
  await page.getByLabel('收藏备注').fill('上线前检查')
  await page.getByLabel('关联项目与任务').selectOption({ label: `任务 · ${taskTitle}` })
  await page.getByRole('button', { name: '保存收藏' }).click()
  await page.getByRole('heading', { name: title }).waitFor()
  const link = page.getByRole('link', { name: /在 AFFiNE 中打开/ }).first()
  check((await link.getAttribute('href')) === `${AFFINE}/workspace/ws1/doc-${run}?mode=page`, '链接应规范化：保留 mode，去掉 refreshKey 与片段')
  check(writes.length === 1, '应只提交一次')
  step('新增收藏：不合法的链接在前端被拦下；保存后链接已规范化，关联了任务')

  // ---------------------------------------------------------------- 任务抽屉里的相关文档
  await page.goto(`${base}/tasks`)
  await page.locator('button.task-title', { hasText: taskTitle }).click()
  await page.getByRole('region', { name: '相关文档' }).getByText(title).waitFor()
  step('任务抽屉的“相关文档”显示关联的收藏')

  // ---------------------------------------------------------------- 日历新建 / 编辑（S4-02）
  await page.goto(`${base}/calendar`)
  await page.getByLabel('查看日期').fill(tomorrow)
  await page.getByLabel('显示时区').fill(ZONE)
  await page.getByRole('button', { name: '应用日历范围' }).click()
  const eventTitle = `浏览器日程 ${run}`
  await page.getByRole('button', { name: '新建日程' }).click()
  await page.getByLabel('日程标题').fill(eventTitle)
  await page.getByLabel('事件时区').fill(ZONE)
  await page.getByLabel('开始时间').fill(`${tomorrow}T10:00`)
  await page.getByLabel('结束时间').fill(`${tomorrow}T11:00`)
  await page.getByRole('button', { name: '保存日程' }).click()
  await page.getByRole('button', { name: eventTitle, exact: true }).waitFor()
  await page.getByRole('button', { name: eventTitle, exact: true }).click()
  await page.getByLabel('日程标题').fill(`${eventTitle} 改`)
  await page.getByLabel('日程地点').fill('三楼会议室')
  await page.getByRole('button', { name: '保存日程' }).click()
  await page.getByRole('button', { name: `${eventTitle} 改`, exact: true }).waitFor()
  const day = Date.parse(`${tomorrow}T00:00:00+08:00`)
  const stored = await api(page, workspace, 'GET', `/api/v1/calendar/events?start=${new Date(day).toISOString()}&end=${new Date(day + 86400000).toISOString()}`)
  const saved = stored.body.items.find((item) => item.title === `${eventTitle} 改`)
  // 当地 10:00（UTC+8）= 当天零点之后 10 小时
  check(saved && saved.start_at.startsWith(new Date(day + 10 * 3600000).toISOString().slice(0, 16)), `开始时间应按 ${ZONE} 换算成 UTC 保存：${saved && saved.start_at}`)
  check(saved.location === '三楼会议室' && saved.version === 2, '编辑应保存地点并递增版本')
  step(`日历：新建并编辑日程，时间按 ${ZONE} 换算后保存`)

  // ---------------------------------------------------------------- 首页“最近文档”“近期日程”（规范 8.3）
  const widgets = [
    { widget_type: 'recent_docs', config: { limit: 5 }, layout: { desktop: { x: 0, y: 8, w: 6, h: 4 }, mobile: { y: 13, h: 4 } } },
    { widget_type: 'calendar_agenda', config: { days: 7, limit: 5 }, layout: { desktop: { x: 6, y: 8, w: 6, h: 4 }, mobile: { y: 17, h: 4 } } },
  ]
  for (const widget of widgets) {
    const created = await api(page, workspace, 'POST', `/api/v1/dashboards/${dashboard}/widgets`, widget)
    check(created.status === 201, `添加组件失败：${created.status} ${JSON.stringify(created.body).slice(0, 200)}`)
  }
  await page.goto(`${base}/`)
  await page.getByRole('article', { name: '最近文档' }).getByText(title).waitFor()
  await page.getByRole('article', { name: '近期日程' }).getByText(`${eventTitle} 改`).waitFor()
  step('首页“最近文档”“近期日程”组件显示真实数据')

  // ---------------------------------------------------------------- 连接 GitHub（S4-04，模拟 GitHub）
  const token = 'github_pat_e2e_' + randomBytes(20).toString('hex')
  startFakeGitHub(token)
  check(await fakeGitHubReady(), '模拟 GitHub 没有就绪')
  await page.goto(`${base}/settings/integrations`)
  await page.getByRole('button', { name: '新建 GitHub 连接' }).click()
  await page.getByLabel('GitHub Token', { exact: true }).fill(token)
  await page.getByLabel('GitHub 仓库列表').fill(FAKE_REPO)
  await page.getByRole('button', { name: '保存 GitHub 连接' }).click()
  await page.getByText('连接已保存，请测试连接').waitFor()
  const card = page.locator('section.integration-card').filter({ has: page.getByRole('heading', { name: 'GitHub', exact: true }) })
  await card.getByRole('button', { name: '测试连接' }).click()
  const checks = page.getByRole('region', { name: '连接检查结果' })
  await checks.getByText('必需检查通过').waitFor({ timeout: 30000 })
  await checks.getByText('仓库权限检查通过').waitFor()
  check(!(await page.content()).includes(token), '页面上不应出现 GitHub Token')
  step('设置页连接 GitHub：细粒度 Token 保存后测试连接通过，逐仓库权限检查通过，页面不显示 Token')

  // ---------------------------------------------------------------- 开发者页（仓库 / Issue / Actions 与故障摘要）
  await page.goto(`${base}/developer`)
  check(await refreshUntil(page, '刷新 GitHub 数据', page.getByRole('heading', { name: FAKE_REPO })), '开发者页应显示同步来的仓库')
  await page.getByRole('button', { name: 'Issues', exact: true }).click()
  await page.getByRole('heading', { name: '验收：登录失败' }).waitFor()
  check(await page.getByRole('heading', { name: '验收：修复登录' }).count() === 0, 'Issue 列表里不应出现 PR')
  await page.getByRole('button', { name: 'Actions', exact: true }).click()
  await page.getByText('test：Run pytest').waitFor()
  step('开发者页：仓库、Issue（不含 PR）、Actions 运行与故障摘要（失败的 job / step）')

  // ---------------------------------------------------------------- 限流提示（A17）
  await fakeGitHub('POST', '/_fake/control', { rate_limit: 'primary', reset_in: 120 })
  await page.goto(`${base}/settings/integrations`)
  // CI 里每分钟对账一次（RECONCILE_INTERVAL_MINUTES=1），可能和这一步撞上：
  // - 对账先碰到限流：按钮按设计被禁用（限流期间不能手动同步）；
  // - 对账正持有同步锁：手动同步返回 409 SYNC_IN_PROGRESS，页面提示“同步正在进行”。
  // 这几种情况下都会有一次同步碰到限流（最迟是下一次对账；限流持续 120 秒），所以下面最多等 90 秒
  const syncButton = card.getByRole('button', { name: '手动同步' })
  await syncButton.waitFor()
  if (await syncButton.isEnabled()) {
    const clicked = await syncButton.click({ timeout: 5000 }).then(() => true, () => false)
    if (clicked) await page.getByText(/同步已排队，请稍后刷新状态|同步正在进行，请稍后刷新状态/).waitFor()
  }
  await page.goto(`${base}/developer`)
  check(await refreshUntil(page, '刷新 GitHub 数据', page.getByText(/GitHub 限流中，预计/), 90000), '开发者页应提示 GitHub 限流中与预计恢复时间')
  check(await page.getByRole('heading', { name: FAKE_REPO }).count() === 1, '限流时仍显示已同步的仓库，不显示成空列表')
  await fakeGitHub('POST', '/_fake/control', { rate_limit: null })
  step('A17：GitHub 限流时开发者页显示“限流中”与预计恢复时间，列表照常显示')

  // ---------------------------------------------------------------- 顶栏全局搜索（S4-05）
  await openSearch(page, run)
  await page.getByRole('region', { name: '搜索结果：收藏文档' }).getByText(title).waitFor()
  const events = page.getByRole('region', { name: '搜索结果：日程' })
  await events.getByText(`${eventTitle} 改`).waitFor()
  await page.getByLabel('搜索页面与组件').fill(FAKE_REPO)
  await page.getByRole('region', { name: '搜索结果：仓库' }).getByText(FAKE_REPO).waitFor()
  await page.getByLabel('搜索页面与组件').fill(run)
  await events.getByRole('link').first().click()
  await page.waitForURL(new RegExp(`/calendar\\?date=${tomorrow}`))
  await page.getByRole('button', { name: `${eventTitle} 改`, exact: true }).waitFor()
  step('顶栏搜索：收藏、日程、GitHub 仓库按类型分组显示；点日程跳到日历对应日期')

  // ---------------------------------------------------------------- 移动端
  await page.setViewportSize({ width: 390, height: 844 })
  for (const route of ['/knowledge', '/calendar']) {
    await page.goto(`${base}${route}`)
    await page.locator('h1').waitFor()
    check(await noHorizontalScroll(page), `${route} 在 390px 宽度下不应出现横向滚动`)
  }
  await page.setViewportSize({ width: 1440, height: 1000 })
  step('390px 宽度下知识库与日历页无横向滚动')

  // ---------------------------------------------------------------- viewer 只读
  const viewer = await login(browser, `viewer4-${run}@browser.example.com`, problems, requests)
  await viewer.page.getByLabel('当前工作空间').selectOption({ label: 'Owner 的空间' })
  await viewer.page.goto(`${base}/knowledge`)
  await viewer.page.getByRole('heading', { name: title }).waitFor()
  check(await viewer.page.getByRole('button', { name: '新增收藏' }).count() === 0, 'viewer 不应看到新增收藏')
  check(await viewer.page.getByRole('button', { name: '管理知识来源' }).count() === 0, 'viewer 不应看到管理知识来源')
  check(await viewer.page.getByRole('button', { name: `删除收藏 ${title}` }).count() === 0, 'viewer 不应看到删除收藏')
  await viewer.page.goto(`${base}/calendar`)
  await viewer.page.getByLabel('查看日期').fill(tomorrow)
  await viewer.page.getByLabel('显示时区').fill(ZONE)
  await viewer.page.getByRole('button', { name: '应用日历范围' }).click()
  await viewer.page.getByRole('button', { name: `${eventTitle} 改`, exact: true }).click()
  await viewer.page.getByText('当前角色只读。').waitFor()
  check(await viewer.page.getByRole('button', { name: '新建日程' }).count() === 0, 'viewer 不应看到新建日程')
  check(await viewer.page.getByRole('button', { name: '保存日程' }).count() === 0, 'viewer 不应看到保存日程')
  check(await viewer.page.getByRole('button', { name: '删除日程' }).count() === 0, 'viewer 不应看到删除日程')
  await viewer.page.goto(`${base}/developer`)
  await viewer.page.getByRole('heading', { name: FAKE_REPO }).waitFor()
  check(await viewer.page.getByRole('link', { name: '重新连接 GitHub' }).count() === 0, 'viewer 不应看到连接管理入口')
  step('viewer：能看收藏、日程与开发者页，没有新增、管理、删除入口，日程表单只读')

  // ---------------------------------------------------------------- 删除日程与收藏（A10）
  await page.goto(`${base}/calendar`)
  await page.getByLabel('查看日期').fill(tomorrow)
  await page.getByLabel('显示时区').fill(ZONE)
  await page.getByRole('button', { name: '应用日历范围' }).click()
  await page.getByRole('button', { name: `${eventTitle} 改`, exact: true }).click()
  await page.getByRole('button', { name: '删除日程' }).click()
  await page.getByText('日程已删除').waitFor()
  check(await page.getByRole('button', { name: `${eventTitle} 改`, exact: true }).count() === 0, '删除后日程应从日历消失')
  await page.goto(`${base}/knowledge`)
  await page.getByRole('button', { name: `删除收藏 ${title}` }).click()
  await page.getByText('收藏已删除').waitFor()
  check(await page.getByRole('heading', { name: title }).count() === 0, '删除后收藏应从列表消失')
  const left = await searchTitles(page, workspace, run)
  check(!left.includes(title) && !left.includes(`${eventTitle} 改`), `删除后搜索不应再返回：${left}`)
  // 被关联的任务标题里也有 run：等任务分组出现（结果已返回），再确认收藏与日程分组都没有了
  await openSearch(page, run)
  await page.getByRole('region', { name: '搜索结果：任务' }).getByText(taskTitle).waitFor()
  check(await page.getByRole('region', { name: '搜索结果：收藏文档' }).count() === 0, '删除后搜索框里不应再有收藏')
  check(await page.getByRole('region', { name: '搜索结果：日程' }).count() === 0, '删除后搜索框里不应再有日程')
  step('A10：删除日程与收藏后，页面、搜索接口与顶栏搜索框都不再出现')

  // ---------------------------------------------------------------- DoD：不请求 AFFiNE
  const affine = requests.filter((url) => url.startsWith(AFFINE))
  check(affine.length === 0, `浏览器不应请求 AFFiNE：${affine.join(', ')}`)
  step('DoD：全程没有向 AFFiNE 地址发出请求')

  // 收尾：停用本次的 GitHub 集成。模拟服务随脚本结束而删除，留着启用的集成会让周期对账一直连不上它，
  // 熔断器打开后同一主机的其他集成也会被拒绝（本机重复运行时出现过）
  const listed = await api(page, workspace, 'GET', '/api/v1/integrations')
  const github = listed.body.items.find((item) => item.provider === 'github')
  const disabled = await api(page, workspace, 'PATCH', `/api/v1/integrations/${github.id}`, { version: github.version, enabled: false })
  check(disabled.status === 200, `停用 GitHub 集成失败：${disabled.status}`)

  await browser.close()
  if (problems.length) throw new Error(`页面错误：${problems.join('; ')}`)
  console.log('\n全部通过（页面无错误）')
})().catch((error) => {
  console.error(`失败  ${error.message}`)
  process.exitCode = 1
}).finally(() => {
  stopFakeGitHub()
  process.exit()
})
