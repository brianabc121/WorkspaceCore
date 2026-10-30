// Sprint 3 浏览器验收（T-0023）：真实后端 + 真实 Vikunja 2.7.1 上的任务中心——
// 连接 Vikunja → 测试连接 → 任务列表 / 看板 → 新建 / 编辑任务 → 在 Vikunja 原生修改 → 工作台显示更新 →
// 首页任务组件显示真实数据 → 删除任务 → viewer 只读。
//
// 前提：Compose 全栈已启动并迁移（make up && make migrate），Vikunja 已启动（make vikunja-up）。
// 不要求空库：每次用新的工作台用户、Vikunja 用户与项目；工作台用户由同目录的 seed.py 写入。
// 用法（依赖由同目录的 package.json / package-lock.json 锁定为 Playwright 1.63.0）：
//   cd tests/e2e && npm ci && npm run browser:install    # 首次：安装依赖与 Chromium
//   node sprint3_browser.cjs [BASE_URL]                    # 默认 http://127.0.0.1:${HTTP_PORT:-8080}
// Vikunja 从本机访问的地址默认 http://127.0.0.1:${VIKUNJA_PORT:-3456}（VIKUNJA_URL 可覆盖）。
// CI（.github/workflows/ci-infra.yml）与本机共用。
const { execFileSync } = require('node:child_process')
const { randomBytes } = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require('playwright')

const ROOT = path.resolve(__dirname, '../..')
const base = (process.argv[2] || `http://127.0.0.1:${process.env.HTTP_PORT || '8080'}`).replace(/\/$/, '')
const vikunjaUrl = (process.env.VIKUNJA_URL || `http://127.0.0.1:${envValue('VIKUNJA_PORT') || '3456'}`).replace(/\/$/, '')
const password = 'e2e sprint3 browser pass'
const run = randomBytes(4).toString('hex')
const WEBHOOK_TIMEOUT = 30000

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

// 直接访问 Vikunja：模拟用户在原生界面里的操作
let vikunjaAuth = {}
async function vikunja(method, pathname, body, { merge = false } = {}) {
  const headers = { Accept: 'application/json', ...vikunjaAuth }
  if (body !== undefined) headers['Content-Type'] = merge ? 'application/merge-patch+json' : 'application/json'
  const response = await fetch(`${vikunjaUrl}/api/v2${pathname}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) })
  const text = await response.text()
  if (!response.ok) throw new Error(`Vikunja ${method} ${pathname} 失败：${response.status} ${text.slice(0, 200)}`)
  return text ? JSON.parse(text) : null
}
async function vikunjaTitles(projectId) {
  const page = await vikunja('GET', `/projects/${projectId}/tasks?per_page=50`)
  return (page.items || []).map((task) => task.title)
}
function createVikunjaUser(username, secret) {
  const project = envValue('COMPOSE_PROJECT_NAME') || 'my-workspace'
  execFileSync('docker', [
    'compose', '-p', `${project}-vikunja`, '-f', 'infra/vikunja/compose.yaml', '--env-file', '.env',
    'exec', '-T', 'vikunja', '/app/vikunja/vikunja', 'user', 'create',
    '-u', username, '-e', `${username}@browser.example.com`, '-p', secret,
  ], { cwd: ROOT, env: { ...process.env, WORKSPACE_PROJECT: project }, stdio: 'pipe' })
}

async function login(browser, email, problems, viewport = { width: 1440, height: 1000 }) {
  const context = await browser.newContext({ viewport })
  const page = await context.newPage()
  page.on('pageerror', (error) => problems.push(error.message))
  page.on('console', (message) => {
    // 登录前查询当前用户的 401 是预期响应，浏览器会把它记为资源加载失败
    if (message.type() === 'error' && !/\b401\b/.test(message.text())) problems.push(message.text())
  })
  page.on('dialog', (dialog) => dialog.accept())
  await page.goto(`${base}/login`)
  await page.getByLabel('邮箱').fill(email)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '登录' }).click()
  await page.waitForURL(`${base}/`)
  return { context, page }
}
// 点“刷新任务”直到列表里出现（或不再出现）某个标题：原生改动经 Webhook 回到投影需要一两秒
async function waitForTitle(page, title, present = true) {
  const deadline = Date.now() + WEBHOOK_TIMEOUT
  while (Date.now() < deadline) {
    await page.getByRole('button', { name: '刷新任务' }).click()
    await page.waitForTimeout(800)
    const count = await page.locator('button.task-title', { hasText: title }).count()
    if (present ? count > 0 : count === 0) return true
  }
  return false
}

;(async () => {
  // ---------------------------------------------------------------- Vikunja 用户、受限 Token 与项目
  const username = `browser${run}`
  const vikunjaPassword = `e2e-${randomBytes(12).toString('hex')}`
  createVikunjaUser(username, vikunjaPassword)
  const session = await vikunja('POST', '/login', { username, password: vikunjaPassword })
  vikunjaAuth = { Authorization: `Bearer ${session.token}` }
  const expires = new Date(Date.now() + 86400000).toISOString().replace(/\.\d+Z$/, 'Z')
  const permissions = {
    projects: ['read_all', 'read_one'],
    tasks: ['create', 'read_all', 'read_one', 'update', 'delete'],
    projects_webhooks: ['create', 'read_all', 'update', 'delete'],
    labels: ['read_all'],
  }
  const { token } = await vikunja('POST', '/tokens', { title: `browser-${run}`, expires_at: expires, permissions })
  const project = await vikunja('POST', '/projects', { title: `浏览器验收 ${run}` })
  const native = await vikunja('POST', `/projects/${project.id}/tasks`, { title: '原生任务' })

  const [, workspace] = seed('user', `owner-${run}@browser.example.com`, 'Owner')
  const [viewerId] = seed('user', `viewer-${run}@browser.example.com`, 'Viewer')
  seed('member', workspace, viewerId, 'viewer')

  const browser = await chromium.launch()
  const problems = []
  const step = (text) => console.log(`通过  ${text}`)
  const { page } = await login(browser, `owner-${run}@browser.example.com`, problems)

  // ---------------------------------------------------------------- 连接 Vikunja（S3-02）
  await page.goto(`${base}/settings/integrations`)
  await page.getByRole('button', { name: '新建连接' }).click()
  await page.getByLabel('服务地址').fill('http://vikunja:3456')
  await page.getByLabel('浏览器访问地址').fill(vikunjaUrl)
  await page.getByLabel('API Token').fill(token)
  await page.getByRole('button', { name: '保存连接' }).click()
  await page.getByText('连接已保存，请测试连接').waitFor()
  check(!(await page.content()).includes(token), '页面上不应出现 Token')
  await page.getByRole('button', { name: '测试连接' }).click()
  await page.getByRole('heading', { name: '必需检查通过' }).waitFor({ timeout: 30000 })
  await page.getByRole('button', { name: '刷新连接' }).click()
  await page.getByText('状态：已连接').waitFor()
  check(!(await page.content()).includes(token), '测试与刷新之后页面上仍不应出现 Token')
  step('设置页连接 Vikunja：保存后测试连接通过、状态为已连接，页面不显示 Token')

  // ---------------------------------------------------------------- 任务列表 / 看板（S3-03）
  await page.goto(`${base}/tasks`)
  await page.locator('button.task-title', { hasText: '原生任务' }).waitFor()
  check(await page.getByText('数据已同步').count() > 0, '同步状态应为已同步')
  const link = page.getByRole('link', { name: /在 Vikunja 中打开/ }).first()
  check((await link.getAttribute('href')).startsWith(`${vikunjaUrl}/tasks/`), '任务应链接到 Vikunja 原生界面')
  step('任务页显示首次同步的 Vikunja 任务，标明来源并可跳到原生界面')

  // ---------------------------------------------------------------- 新建（S3-04：带 Idempotency-Key）
  const keys = []
  page.on('request', (request) => {
    if (request.url().endsWith('/api/v1/tasks') && request.method() === 'POST') keys.push(request.headers()['idempotency-key'])
  })
  await page.getByRole('button', { name: '新建任务' }).click()
  await page.getByLabel('任务标题').fill('浏览器新建')
  await page.getByLabel('任务项目').selectOption({ label: project.title })
  await page.getByLabel('任务优先级').selectOption({ index: 3 })
  // 截止时间设为今天，首页“今日任务”应显示它
  const today = new Date(Date.now() + 60 * 60 * 1000)
  const local = new Date(today.getTime() - today.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
  await page.getByLabel('截止时间', { exact: true }).fill(local)
  await page.getByRole('button', { name: '创建任务' }).click()
  await page.locator('button.task-title', { hasText: '浏览器新建' }).waitFor()
  const created = (await vikunjaTitles(project.id)).filter((title) => title === '浏览器新建').length
  check(created === 1, `Vikunja 里应恰好有 1 个新建的任务，实际 ${created}`)
  check(keys.length === 1 && /^[0-9a-f-]{36}$/.test(keys[0]), '创建请求应带 UUID 格式的 Idempotency-Key')
  step('新建任务抽屉：提交一次、带 Idempotency-Key，Vikunja 里只有一个任务，列表随即显示')

  // ---------------------------------------------------------------- 编辑（带 version）
  await page.locator('button.task-title', { hasText: '浏览器新建' }).click()
  await page.getByLabel('任务标题').fill('浏览器修改')
  await page.getByLabel('已完成任务').check()
  await page.getByRole('button', { name: '保存任务' }).click()
  await page.locator('button.task-title', { hasText: '浏览器修改' }).waitFor()
  const edited = await vikunja('GET', `/projects/${project.id}/tasks?per_page=50`)
  const editedTask = edited.items.find((task) => task.title === '浏览器修改')
  check(editedTask && editedTask.done === true, 'Vikunja 里的任务应已改名并标为完成')
  step('编辑抽屉：修改标题并标为完成，Vikunja 同步变化')

  // ---------------------------------------------------------------- 原生修改 → 工作台更新（DoD）
  await vikunja('PATCH', `/tasks/${native.id}`, { title: '原生界面里改的' }, { merge: true })
  check(await waitForTitle(page, '原生界面里改的'), '在 Vikunja 里的修改应出现在工作台任务页')
  step('DoD：在 Vikunja 原生修改任务，工作台任务页显示更新（Webhook）')

  // ---------------------------------------------------------------- 看板
  await page.getByRole('button', { name: '看板', exact: true }).click()
  const open = page.getByRole('region', { name: '未完成' })
  const done = page.getByRole('region', { name: '已完成' })
  await open.locator('button.task-title', { hasText: '原生界面里改的' }).waitFor()
  await done.locator('button.task-title', { hasText: '浏览器修改' }).waitFor()
  step('看板视图按完成状态分列')

  // ---------------------------------------------------------------- 首页任务组件接真实数据
  await page.goto(`${base}/`)
  const summary = page.getByRole('article', { name: '待办汇总' })
  await summary.locator('.summary-number').waitFor()
  check((await summary.locator('.summary-number').textContent()).trim() === '1', '待办汇总应为 1（原生任务未完成）')
  step('首页待办汇总显示真实数据')

  // ---------------------------------------------------------------- 删除（二次确认）
  await page.goto(`${base}/tasks`)
  await page.getByRole('button', { name: '列表', exact: true }).click()
  await page.locator('button.task-title', { hasText: '浏览器修改' }).click()
  await page.getByRole('button', { name: '删除任务' }).click()
  check(await waitForTitle(page, '浏览器修改', false), '删除后任务应从列表消失')
  check(!(await vikunjaTitles(project.id)).includes('浏览器修改'), 'Vikunja 里应已删除')
  step('删除任务（确认后）：工作台与 Vikunja 里都已删除')

  // ---------------------------------------------------------------- 移动端
  await page.setViewportSize({ width: 390, height: 844 })
  await page.locator('button.task-title', { hasText: '原生界面里改的' }).waitFor()
  check(!(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)), '移动端不应出现横向滚动')
  step('390px 宽度下任务页无横向滚动')

  // ---------------------------------------------------------------- viewer 只读
  const viewer = await login(browser, `viewer-${run}@browser.example.com`, problems)
  await viewer.page.getByLabel('当前工作空间').selectOption({ label: 'Owner 的空间' })
  await viewer.page.goto(`${base}/tasks`)
  await viewer.page.locator('button.task-title', { hasText: '原生界面里改的' }).waitFor()
  check(await viewer.page.getByRole('button', { name: '新建任务' }).count() === 0, 'viewer 不应看到新建任务')
  await viewer.page.locator('button.task-title', { hasText: '原生界面里改的' }).click()
  check(await viewer.page.getByRole('button', { name: '保存任务' }).count() === 0, 'viewer 不应看到保存任务')
  check(await viewer.page.getByRole('button', { name: '删除任务' }).count() === 0, 'viewer 不应看到删除任务')
  step('viewer：能看任务，没有新建、保存、删除入口')

  await browser.close()
  if (problems.length) throw new Error(`页面错误：${problems.join('; ')}`)
  console.log('\n全部通过（页面无错误）')
})().catch((error) => {
  console.error(`失败  ${error.message}`)
  process.exit(1)
})
