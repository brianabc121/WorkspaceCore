// 首页改版浏览器验收（T-0163 ~ T-0165，docs/v2/HOME_PLAN.md）：真实后端 + 真实前端——
// 新工作空间的首页是效果图的五个组件 → 准备待办 / 进行中 / 待查看 / 阻塞四种任务和两条日程 →
// 工作概况的数字、任务进展的“需要你关注 N 项”、页签筛选、阻塞 Issue 标题 → “查看结果”打开任务、确认结果后
// 待查看减少 → 我的待办添加（指派给自己）并勾选完成 → 提醒与日程就地添加 → 自定义首页与恢复默认首页 →
// viewer 只读；手机宽度不横向滚动、暗色主题正常。
//
// 前提：Compose 全栈已启动并迁移（make up && make migrate）。没有 OpenClaw：智能体完成任务由 seed.py review 模拟。
// 用法（依赖由同目录的 package.json / package-lock.json 锁定）：
//   cd tests/e2e && npm ci && npm run browser:install    # 首次：安装依赖与 Chromium
//   node home_browser.cjs [BASE_URL]                       # 默认 http://127.0.0.1:${HTTP_PORT:-8080}
// CI（.github/workflows/ci-infra.yml 的 compose job）与本机共用。不要求空库：每次新建工作台用户。
const { execFileSync } = require('node:child_process')
const { randomBytes } = require('node:crypto')
const path = require('node:path')
const { chromium } = require('playwright')

const base = (process.argv[2] || `http://127.0.0.1:${process.env.HTTP_PORT || 8080}`).replace(/\/$/, '')
const password = 'e2e home browser pass'
const run = randomBytes(4).toString('hex')
const ownerEmail = `home-owner-${run}@browser.example.com`
const viewerEmail = `home-viewer-${run}@browser.example.com`

function seed(...args) {
  return execFileSync('python3', [path.join(__dirname, 'seed.py'), ...args], {
    encoding: 'utf8',
    env: { ...process.env, E2E_PASSWORD: password },
  }).trim().split(/\s+/)
}
function check(condition, message) { if (!condition) throw new Error(message) }
const step = (text) => console.log(`通过  ${text}`)

async function api(page, workspace, method, pathname, body, extra = {}) {
  return page.evaluate(async ({ method, pathname, body, workspace, extra }) => {
    const csrf = document.cookie.split('; ').find((item) => item.startsWith('ws_csrf='))?.slice(8)
    const headers = { 'X-Workspace-Id': workspace, ...(csrf ? { 'X-CSRF-Token': decodeURIComponent(csrf) } : {}), ...extra }
    if (body !== undefined) headers['Content-Type'] = 'application/json'
    const response = await fetch(pathname, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) })
    return { status: response.status, body: response.status === 204 ? null : await response.json() }
  }, { method, pathname, body, workspace, extra })
}
const idem = () => ({ 'Idempotency-Key': crypto.randomUUID() })

function watch(page, problems) {
  page.on('pageerror', (error) => problems.push(error.message))
  page.on('response', (response) => {
    const pathname = new URL(response.url()).pathname
    // 顶栏的 IM 角标：没有部署 OpenIM 时 503（与首页无关，降级显示）
    if (response.status() >= 500 && !(response.status() === 503 && pathname.startsWith('/api/v1/im/'))) problems.push(`${response.status()} ${response.request().method()} ${pathname}`)
  })
  page.on('console', (message) => {
    // 登录前的 401、viewer 确认结果的 403（本脚本故意请求的）、上面忽略的 IM 503 都是预期内的
    if (message.type() === 'error' && !/\b(401|403|503)\b/.test(message.text())) problems.push(message.text())
  })
}
async function login(browser, email, problems, options = {}) {
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 }, locale: 'zh-CN', ...options })
  const page = await context.newPage()
  watch(page, problems)
  await page.goto(`${base}/login`)
  await page.getByLabel('邮箱').fill(email)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '登录' }).click()
  await page.waitForURL(`${base}/`)
  return { context, page }
}
async function tile(page, key) {
  return (await page.locator(`[data-tile="${key}"] .summary-value`).textContent()).trim()
}
async function waitTile(page, key, value) {
  await page.waitForFunction(([k, v]) => document.querySelector(`[data-tile="${k}"] .summary-value`)?.textContent?.trim() === v, [key, value])
}
const noHorizontalScroll = (page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)

;(async () => {
  const [ownerId, workspace] = seed('user', ownerEmail, 'Hana')
  const [viewerId] = seed('user', viewerEmail, 'Vic')
  seed('member', workspace, viewerId, 'viewer')
  const problems = []
  const browser = await chromium.launch()
  const { page } = await login(browser, ownerEmail, problems)

  // ---------------------------------------------------------------- 默认首页
  await page.getByRole('heading', { name: '工作总览', level: 1 }).waitFor()
  for (const title of ['工作概况', '任务进展', 'AI 新闻速览', '我的待办', '提醒与日程']) await page.getByRole('article', { name: title }).waitFor()
  await page.getByRole('button', { name: '刷新首页' }).waitFor()
  await page.getByRole('button', { name: '自定义首页', exact: true }).waitFor()
  await waitTile(page, 'todo', '0')
  step('新工作空间的首页：工作总览页头（刷新、自定义首页）与效果图的五个组件，待办为 0')

  // ---------------------------------------------------------------- 四种状态的任务与日程
  const project = (await api(page, workspace, 'POST', '/api/v1/projects', { title: `首页验收 ${run}` })).body
  const create = async (title, extra = {}) => {
    const response = await api(page, workspace, 'POST', '/api/v1/tasks', { project_id: project.id, title, ...extra }, idem())
    check(response.status === 201, `新建任务失败：${response.status} ${JSON.stringify(response.body).slice(0, 200)}`)
    return response.body
  }
  await create('还没开始的任务')
  const running = await create('正在做的任务', { assignee_user_id: ownerId })
  const started = await api(page, workspace, 'POST', `/api/v1/tasks/${running.id}/runs`, {}, idem())
  check([200, 201].includes(started.status), `开始执行失败：${started.status}`)
  const blocked = await create('被卡住的任务')
  const issue = await api(page, workspace, 'POST', `/api/v1/projects/${project.id}/issues`, { title: '等待补充访问权限', kind: 'blocker', task_ids: [blocked.id] })
  check(issue.status === 201, `开阻塞 Issue 失败：${issue.status}`)
  const review = await create('智能体做完的方案')
  seed('review', workspace, review.id, '设计助手')
  const now = new Date(), soon = new Date(now.getTime() + 2 * 3600000)
  for (const [title, start] of [['查看发布结果', soon], ['项目进度同步', new Date(now.getTime() + 20 * 3600000)]]) {
    const created = await api(page, workspace, 'POST', '/api/v1/calendar/events', { title, timezone: 'UTC', all_day: false, start_at: start.toISOString(), end_at: new Date(start.getTime() + 1800000).toISOString(), description: '', location: '' })
    check(created.status === 201, `新建日程失败：${created.status}`)
  }
  await page.getByRole('button', { name: '刷新首页' }).click()
  await waitTile(page, 'review', '1')
  check(await tile(page, 'todo') === '1' && await tile(page, 'in_progress') === '1' && await tile(page, 'blocked') === '1', '工作概况的待办 / 进行中 / 阻塞应各为 1')
  check(Number(await tile(page, 'reminders')) >= 1, '近期提醒应至少有今天的那条')
  const progress = page.getByRole('article', { name: '任务进展' })
  await progress.getByText('需要你关注 2 项').waitFor()
  const rows = progress.locator('tbody tr')
  check(await rows.count() === 4, `全部页签应有 4 行，实际 ${await rows.count()}`)
  check(await rows.nth(0).getAttribute('data-status') === 'review' && await rows.nth(1).getAttribute('data-status') === 'blocked', '待查看与阻塞应排在前面')
  await progress.getByText('设计助手').waitFor()
  await progress.getByRole('button', { name: /^阻塞/ }).click()
  // 切换页签时先保留上一页（不闪空），新数据到了才只剩阻塞的任务
  await page.waitForFunction(() => document.querySelectorAll('article[aria-label="任务进展"] tbody tr').length === 1)
  await progress.locator('tbody tr[data-status="blocked"]').getByText('等待补充访问权限').waitFor()
  check(await progress.getByRole('link', { name: '处理阻塞：被卡住的任务' }).getAttribute('href') === `/issues/${issue.body.id}`, '处理阻塞应链到阻塞 Issue')
  await progress.getByRole('button', { name: /^全部/ }).click()
  step('工作概况四种状态各 1、近期提醒有日程；任务进展“需要你关注 2 项”、待查看与阻塞在前、执行者名字、阻塞页签与 Issue 标题、处理阻塞链到 Issue')

  // ---------------------------------------------------------------- 查看结果 → 确认结果
  await progress.getByRole('link', { name: '查看结果：智能体做完的方案' }).click()
  await page.waitForURL(/\/tasks\?task=/)
  const confirm = page.getByRole('button', { name: '确认结果' })
  await confirm.waitFor()
  await page.getByText('完成说明：方案已写进任务 README').waitFor()
  await confirm.click()
  await page.getByText('已确认结果').waitFor()
  await page.goto(`${base}/`)
  await waitTile(page, 'review', '0')
  await page.getByRole('article', { name: '任务进展' }).getByText('需要你关注 1 项').waitFor()
  const events = await api(page, workspace, 'GET', `/api/v1/tasks/${review.id}/events`)
  check(events.body.items[0].kind === 'reviewed', '任务动态应记“确认了结果”')
  step('“查看结果”打开任务，显示完成说明；确认结果后首页待查看为 0、需要关注降为 1，动态记“确认了结果”')

  // ---------------------------------------------------------------- 我的待办
  const todos = page.getByRole('article', { name: '我的待办' })
  await todos.getByText('正在做的任务').waitFor()
  await todos.getByRole('button', { name: '添加待办' }).click()
  await todos.getByLabel('待办内容').fill(`首页添加的待办 ${run}`)
  await todos.getByRole('button', { name: '添加', exact: true }).click()
  await todos.getByText(`已添加待办：首页添加的待办 ${run}`).waitFor()
  await todos.getByRole('link', { name: `首页添加的待办 ${run}` }).waitFor()
  const mine = await api(page, workspace, 'GET', '/api/v1/tasks?assignee=me&status=open&limit=50')
  const added = mine.body.items.find((t) => t.title === `首页添加的待办 ${run}`)
  check(added && added.assignee_user_id === ownerId, '添加的待办应指派给自己')
  await todos.getByRole('checkbox', { name: `完成：首页添加的待办 ${run}` }).check()
  await todos.getByText(`已完成：首页添加的待办 ${run}`).waitFor()
  await todos.getByRole('link', { name: `首页添加的待办 ${run}` }).waitFor({ state: 'detached' })
  check((await api(page, workspace, 'GET', `/api/v1/tasks/${added.id}`)).body.done === true, '勾选后任务应完成')
  step('我的待办：添加的待办指派给自己并出现在列表；勾选后任务完成、从列表消失')

  // ---------------------------------------------------------------- 提醒与日程
  const agenda = page.getByRole('article', { name: '提醒与日程' })
  await agenda.locator('.agenda-item.is-next').getByText('查看发布结果').waitFor()
  await agenda.getByRole('button', { name: '添加提醒' }).click()
  const dialog = page.getByRole('dialog', { name: '添加提醒' })
  await dialog.getByLabel('日程标题').fill(`首页添加的提醒 ${run}`)
  const later = new Date(Date.now() + 3 * 3600000)
  const zone = await page.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone)
  const local = (date) => new Intl.DateTimeFormat('sv-SE', { timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(date).replace(' ', 'T')
  await dialog.getByLabel('开始时间').fill(local(later))
  await dialog.getByLabel('结束时间').fill(local(new Date(later.getTime() + 1800000)))
  await dialog.getByRole('button', { name: '保存日程' }).click()
  await dialog.waitFor({ state: 'detached' })
  await agenda.locator('.agenda-title', { hasText: `首页添加的提醒 ${run}` }).waitFor()
  step('提醒与日程：最近的一条高亮；“添加提醒”就地新建日程，保存后出现在时间轴')

  // ---------------------------------------------------------------- 自定义首页与恢复默认首页
  await page.getByRole('button', { name: '自定义首页', exact: true }).click()
  await page.getByRole('button', { name: '添加组件', exact: true }).waitFor()
  await page.getByRole('button', { name: '取消编辑', exact: true }).click()
  const before = (await api(page, workspace, 'GET', '/api/v1/dashboard/overview')).body
  const removed = await api(page, workspace, 'DELETE', `/api/v1/widgets/${before.widgets.find((w) => w.widget_type === 'my_todos').id}`)
  check(removed.status === 204, '删除组件失败')
  await page.reload()
  await page.getByRole('article', { name: '任务进展' }).waitFor()
  check(await page.getByRole('article', { name: '我的待办' }).count() === 0, '删掉的组件不应再显示')
  page.once('dialog', (d) => d.accept())
  await page.getByRole('button', { name: '更多仪表盘操作' }).click()
  await page.getByRole('menuitem', { name: '恢复默认首页' }).click()
  await page.getByText('已恢复默认首页').waitFor()
  await page.getByRole('article', { name: '我的待办' }).waitFor()
  const after = (await api(page, workspace, 'GET', '/api/v1/dashboard/overview')).body
  check(after.widgets.length === 5 && after.dashboard.version > before.dashboard.version, '恢复后应是默认的五个组件、版本递增')
  step('自定义首页进入编辑模式（添加组件）；删掉组件后“恢复默认首页”换回五个组件')

  // ---------------------------------------------------------------- 手机与暗色
  await page.setViewportSize({ width: 390, height: 844 })
  await page.locator('.mobile-widget-grid').waitFor()
  check(await noHorizontalScroll(page), '手机宽度不应横向滚动')
  await page.getByRole('button', { name: '切换到暗色主题' }).first().click().catch(() => {})
  check(await noHorizontalScroll(page), '暗色主题下手机宽度不应横向滚动')
  step('390px 宽度单列显示、不横向滚动')

  // ---------------------------------------------------------------- viewer 只读
  const viewer = await login(browser, viewerEmail, problems)
  await viewer.page.getByLabel('当前工作空间').selectOption(workspace)
  await viewer.page.getByRole('article', { name: '任务进展' }).getByText('被卡住的任务').waitFor()
  check(await viewer.page.getByRole('button', { name: '添加待办' }).count() === 0, 'viewer 不应看到“添加待办”')
  check(await viewer.page.getByRole('button', { name: '添加提醒' }).count() === 0, 'viewer 不应看到“添加提醒”')
  check(await viewer.page.getByRole('button', { name: '自定义首页', exact: true }).count() === 0, 'viewer 不应看到“自定义首页”')
  const denied = await api(viewer.page, workspace, 'POST', `/api/v1/tasks/${review.id}/review`, { version: '1' })
  check(denied.status === 403, `viewer 确认结果应 403，实际 ${denied.status}`)
  step('viewer 看得到任务进展，没有添加待办 / 添加提醒 / 自定义首页，确认结果 403')

  await browser.close()
  if (problems.length) throw new Error(`页面错误：${problems.join('; ')}`)
  console.log('\n全部通过（页面无错误）')
})().catch((error) => {
  console.error(`失败  ${error.message}`)
  process.exit(1)
})
