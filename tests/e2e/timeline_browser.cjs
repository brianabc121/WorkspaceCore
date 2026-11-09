// 任务时间轴浏览器验收（T-0106，方案 docs/v2/TIMELINE_PLAN.md 第 6 节）：真实后端上的 /tasks/timeline——
// 任务页、项目页的入口 → 全部项目与按项目查看（地址带 project）→ 智能体完成的一段与 token 用量 →
// 人的任务开始、暂停 → 拖动计划中的片段改计划时间 → 任务详情的执行记录 → 手机宽度 → viewer 只读。
//
// 前提：Compose 全栈已启动并迁移（make up && make migrate）。
// 不要求空库：每次用新的工作台用户；用户、成员与“智能体执行过的一段”由同目录的 seed.py 写入
// （Compose 验收里没有真实 OpenClaw；真实实例的上报与交给它执行在 agents_acceptance.py 场景 16）。
// 用法（依赖由同目录的 package.json / package-lock.json 锁定）：
//   cd tests/e2e && npm ci && npm run browser:install    # 首次：安装依赖与 Chromium
//   node timeline_browser.cjs [BASE_URL]                   # 默认 http://127.0.0.1:${HTTP_PORT:-8080}
// CI（.github/workflows/ci-infra.yml）与本机共用。
const { execFileSync } = require('node:child_process')
const { randomBytes, randomUUID } = require('node:crypto')
const path = require('node:path')
const { chromium } = require('playwright')

const base = (process.argv[2] || `http://127.0.0.1:${process.env.HTTP_PORT || '8080'}`).replace(/\/$/, '')
const password = 'e2e timeline browser pass'
const run = randomBytes(4).toString('hex')
const ownerEmail = `tl-owner-${run}@browser.example.com`
const viewerEmail = `tl-viewer-${run}@browser.example.com`
const TZ = 'Asia/Shanghai'
const MIN = 60_000

function seed(...args) {
  return execFileSync('python3', [path.join(__dirname, 'seed.py'), ...args], {
    encoding: 'utf8',
    env: { ...process.env, E2E_PASSWORD: password },
  }).trim().split(/\s+/)
}
function check(condition, message) { if (!condition) throw new Error(message) }
// 浏览器时区（上海）里某个时刻所在的日期：时间轴的 date 参数
const dayOf = (ms) => new Date(ms).toLocaleDateString('sv-SE', { timeZone: TZ })

async function api(page, workspace, method, pathname, body, extra = {}) {
  const result = await page.evaluate(async ({ method, pathname, body, workspace, extra }) => {
    const csrf = document.cookie.split('; ').find((item) => item.startsWith('ws_csrf='))?.slice(8)
    const headers = { 'X-Workspace-Id': workspace, ...(csrf ? { 'X-CSRF-Token': decodeURIComponent(csrf) } : {}), ...extra }
    if (body !== undefined) headers['Content-Type'] = 'application/json'
    const response = await fetch(pathname, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) })
    return { status: response.status, body: response.status === 204 ? null : await response.json() }
  }, { method, pathname, body, workspace, extra })
  check(result.status < 300, `${method} ${pathname} 失败：${result.status} ${JSON.stringify(result.body).slice(0, 300)}`)
  return result.body
}
const write = (page, workspace, method, pathname, body) => api(page, workspace, method, pathname, body, { 'Idempotency-Key': randomUUID() })

// 接口的错误响应按地址记下（浏览器的“资源加载失败”日志不带地址）。
// Compose 验收没有部署 OpenIM：右侧抽屉的助手对话会为唯一的实例打开会话，得到 503（IM 不可用），这是预期的
function watch(page, problems) {
  page.on('pageerror', (error) => problems.push(error.message))
  page.on('response', (response) => {
    const where = `${response.request().method()} ${new URL(response.url()).pathname}`
    if (response.status() >= 500 && !(response.status() === 503 && where === 'POST /api/v1/im/conversations')) problems.push(`${response.status()} ${where}`)
  })
  page.on('console', (message) => {
    if (message.type() === 'error' && !/Failed to load resource/.test(message.text())) problems.push(message.text())
  })
}
async function login(browser, email, problems, viewport = { width: 1440, height: 1000 }) {
  const context = await browser.newContext({ viewport, locale: 'zh-CN', timezoneId: TZ })
  const page = await context.newPage()
  watch(page, problems)
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
async function openTimeline(page, query) {
  await page.goto(`${base}/tasks/timeline?${new URLSearchParams(query)}`)
  await page.getByRole('region', { name: '时间轴', exact: true }).waitFor()
}
const clip = (page, title) => page.getByRole('button', { name: new RegExp(`^${title}，`) })
const detail = (page) => page.getByRole('complementary', { name: '选中的任务' })

;(async () => {
  const [ownerId, workspace] = seed('user', ownerEmail, 'TlOwner')
  const [viewerId] = seed('user', viewerEmail, 'TlViewer')
  seed('member', workspace, viewerId, 'viewer')

  const browser = await chromium.launch()
  const problems = []
  const step = (text) => console.log(`通过  ${text}`)
  const { context, page } = await login(browser, ownerEmail, problems)

  // ---------------------------------------------------------------- 准备：两个项目、三个任务
  // 甲项目：人的任务（计划在现在前后）与智能体完成的任务；乙项目：两小时后开始的计划任务（拖动用）
  const projectA = await write(page, workspace, 'POST', '/api/v1/projects', { title: `时间轴甲 ${run}`, description: '' })
  const projectB = await write(page, workspace, 'POST', '/api/v1/projects', { title: `时间轴乙 ${run}`, description: '' })
  const now = Date.now()
  const iso = (ms) => new Date(ms).toISOString()
  const human = await write(page, workspace, 'POST', '/api/v1/tasks', { project_id: projectA.id, title: `整理发布说明 ${run}`, due_at: iso(now + 60 * MIN) })
  await write(page, workspace, 'PATCH', `/api/v1/tasks/${human.id}`, { version: human.version, planned_start_at: iso(now - 30 * MIN), assignee_user_id: ownerId })
  const agentTask = await write(page, workspace, 'POST', '/api/v1/tasks', { project_id: projectA.id, title: `迁移预演 ${run}` })
  const instanceName = `e2e-claw-${run}`
  const [, usageStart] = seed('timeline', workspace, agentTask.id, instanceName)
  const plannedStart = Math.ceil((now + 120 * MIN) / (15 * MIN)) * 15 * MIN
  const planned = await write(page, workspace, 'POST', '/api/v1/tasks', { project_id: projectB.id, title: `更新巡检脚本 ${run}`, due_at: iso(plannedStart + 60 * MIN) })
  await write(page, workspace, 'PATCH', `/api/v1/tasks/${planned.id}`, { version: planned.version, planned_start_at: iso(plannedStart) })

  // ---------------------------------------------------------------- 入口：任务页“显示方式：时间轴”、项目页“时间轴”
  await page.goto(`${base}/tasks`)
  await page.getByRole('group', { name: '显示方式' }).getByRole('link', { name: '时间轴' }).click()
  await page.waitForURL(/\/tasks\/timeline/)
  await page.getByRole('region', { name: '时间轴', exact: true }).waitFor()
  await page.goto(`${base}/projects`)
  // 项目卡片的“时间轴”在“更多操作”菜单里（T-0168），菜单收起时链接仍在页面上
  await page.getByRole('region', { name: /^项目 / }).first().waitFor()
  await page.getByRole('link', { name: '时间轴', includeHidden: true }).first().waitFor({ state: 'attached' })
  const projectLinks = await page.getByRole('link', { name: '时间轴', includeHidden: true }).evaluateAll((links) => links.map((a) => a.getAttribute('href')))
  check(projectLinks.includes(`/tasks/timeline?project=${projectA.id}`), `项目页应有带 project 的时间轴入口：${projectLinks.join(' ')}`)
  step('入口：任务页“显示方式”切到时间轴，项目页每个项目有“时间轴”')

  // ---------------------------------------------------------------- 全部项目 → 按项目查看
  const agentDay = dayOf(now - 25 * MIN)
  await openTimeline(page, { date: agentDay })
  await clip(page, `迁移预演 ${run}`).waitFor()
  check(await page.getByRole('group', { name: `${instanceName} 的轨道` }).count() === 1, '按执行者分组时应有实例的轨道')
  if (dayOf(plannedStart) === agentDay) check(await clip(page, `更新巡检脚本 ${run}`).count() === 1, '全部项目时应看到乙项目的计划任务')
  await page.getByLabel('项目').selectOption({ label: `时间轴甲 ${run}` })
  await page.waitForURL((url) => url.searchParams.get('project') === projectA.id)
  await clip(page, `迁移预演 ${run}`).waitFor()
  await page.waitForFunction((title) => ![...document.querySelectorAll('.tl-clip')].some((b) => b.getAttribute('aria-label')?.startsWith(title)), `更新巡检脚本 ${run}，`)
  await page.locator('.tl-summary').getByText('这个项目', { exact: true }).waitFor()
  await page.getByLabel('分组').selectOption('project')
  await page.getByRole('group', { name: `时间轴甲 ${run} 的轨道` }).waitFor()
  await page.getByLabel('分组').selectOption('executor')
  step('全部项目与按项目查看：项目下拉写进地址，乙项目的任务不再显示；可以按项目分组')

  // ---------------------------------------------------------------- 智能体完成的一段：token 用量与详情
  await clip(page, `迁移预演 ${run}`).click()
  await detail(page).locator('dd').filter({ hasText: instanceName }).waitFor()
  const usage = await page.locator('.tl-usage').getAttribute('aria-label')
  const total = Number((usage ?? '').match(/合计 (\d+)/)?.[1] ?? 0)
  // 种子从 usageStart 起上报 30 分钟，每分钟 3300 + 10 × i 个 token；时间轴合计的是上海时区这一天里的分钟，
  // 在上海午夜前后运行时这 30 分钟跨两天，只算落在这一天的那部分
  let expected = 0
  for (let i = 0; i < 30; i++) if (dayOf(Date.parse(usageStart) + i * MIN) === agentDay) expected += 3300 + 10 * i
  check(expected > 0 && total === expected, `token 轨道应显示这一天上报的用量合计 ${expected}：${usage}`)
  const runs = (await api(page, workspace, 'GET', `/api/v1/tasks/${agentTask.id}/runs`)).items
  check(runs.length === 1 && runs[0].end_reason === 'done' && runs[0].tokens.total > 0, `智能体那段应已完成并分到 token：${JSON.stringify(runs)}`)
  const tokenText = (await detail(page).innerText()).replace(/\s+/g, ' ')
  check(/token/.test(tokenText) && !/token 未上报/.test(tokenText), `详情应显示这段的 token：${tokenText.slice(0, 200)}`)
  check(await detail(page).getByRole('button', { name: '交给它执行' }).count() === 0, '已完成的任务不应再显示“交给它执行”')
  await page.getByRole('button', { name: '表格视图' }).click()
  await page.locator('.tl-table').getByRole('button', { name: `迁移预演 ${run}` }).waitFor()
  await page.getByRole('button', { name: '表格视图' }).click()
  step('智能体完成的一段：实例轨道、token 用量合计、详情的执行者与 token、表格视图')

  // ---------------------------------------------------------------- 人的任务：开始 → 执行中 → 暂停
  const humanDay = dayOf(now)
  await openTimeline(page, { date: humanDay, project: projectA.id })
  await clip(page, `整理发布说明 ${run}`).click()
  await detail(page).getByRole('button', { name: '开始' }).click()
  await detail(page).getByText('已开始').waitFor()
  await page.getByRole('button', { name: new RegExp(`^整理发布说明 ${run}，执行中`) }).waitFor()
  let humanRuns = (await api(page, workspace, 'GET', `/api/v1/tasks/${human.id}/runs`)).items
  check(humanRuns.length === 1 && !humanRuns[0].ended_at && humanRuns[0].executor_kind === 'user', `开始后应有一段在执行：${JSON.stringify(humanRuns)}`)
  step('人的任务：选中计划片段，点“开始”后变成执行中')

  // 手机宽度（执行中）：正在执行的卡片、底部面板，不横向滚动
  const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, locale: 'zh-CN', timezoneId: TZ, storageState: await context.storageState() })
  const phone = await mobile.newPage()
  watch(phone, problems)
  await openTimeline(phone, { date: humanDay })
  const running = phone.getByRole('region', { name: '正在执行' })
  await running.getByRole('button', { name: new RegExp(`整理发布说明 ${run}`) }).click()
  await detail(phone).getByRole('button', { name: '暂停' }).waitFor()
  check(await noHorizontalScroll(phone), '手机宽度下时间轴不应横向滚动')
  await openTimeline(phone, { date: agentDay, project: projectA.id, scale: 'hour' })
  await phone.getByRole('group', { name: '全天概览' }).waitFor()
  check(await noHorizontalScroll(phone), '手机宽度下小时刻度不应横向滚动')
  await mobile.close()
  step('手机宽度（390）：正在执行的卡片，点开是底部面板；天 / 小时刻度不横向滚动')

  await detail(page).getByRole('button', { name: '暂停' }).click()
  // 精确匹配状态提示：执行片段那一行（“16:57–16:57 · 已暂停”）也含这几个字，可能同时出现
  await detail(page).getByText('已暂停', { exact: true }).waitFor()
  humanRuns = (await api(page, workspace, 'GET', `/api/v1/tasks/${human.id}/runs`)).items
  check(humanRuns.length === 1 && humanRuns[0].ended_at && humanRuns[0].end_reason === 'paused', `暂停后这段应结束：${JSON.stringify(humanRuns)}`)
  await page.getByRole('button', { name: new RegExp(`^整理发布说明 ${run}，已暂停`) }).waitFor()
  step('人的任务：点“暂停”结束这段，片段显示已暂停')

  // ---------------------------------------------------------------- 拖动计划中的片段：往前拖 1 小时，计划开始与截止一起平移
  await openTimeline(page, { date: dayOf(plannedStart), project: projectB.id })
  const target = clip(page, `更新巡检脚本 ${run}`)
  await target.waitFor()
  check(await target.evaluate((el) => el.classList.contains('can-drag')), '计划中的片段对 owner 应可以拖动')
  await target.scrollIntoViewIfNeeded()
  const width = await page.locator('.tl-canvas').evaluate((el) => el.clientWidth)
  const box = await target.boundingBox()
  const x = box.x + Math.min(20, box.width / 2), y = box.y + box.height / 2
  await page.mouse.move(x, y)
  await page.mouse.down()
  await page.mouse.move(x - width / 24, y, { steps: 12 })
  await page.mouse.up()
  await detail(page).getByText('计划时间已更新').waitFor()
  const moved = await api(page, workspace, 'GET', `/api/v1/tasks/${planned.id}`)
  const shiftBy = Date.parse(moved.planned_start_at) - plannedStart
  check(shiftBy <= -45 * MIN && shiftBy >= -75 * MIN && shiftBy % (15 * MIN) === 0, `计划开始应往前移约 1 小时（按 15 分钟对齐）：${moved.planned_start_at}`)
  check(Date.parse(moved.due_at) - Date.parse(moved.planned_start_at) === 60 * MIN, `截止时间应一起平移：${moved.due_at}`)
  step(`拖动计划中的片段：计划开始与截止一起移动 ${shiftBy / MIN} 分钟（15 分钟对齐）`)

  // ---------------------------------------------------------------- 任务详情：执行者与执行记录、在时间轴中查看
  await page.goto(`${base}/tasks?task=${human.id}`)
  const runsPanel = page.getByRole('region', { name: '执行记录' })
  await runsPanel.waitFor()
  await runsPanel.getByText('已暂停').first().waitFor()
  await runsPanel.getByRole('link', { name: '在时间轴中查看' }).click()
  await page.waitForURL((url) => url.pathname === '/tasks/timeline' && url.searchParams.get('task') === human.id)
  await detail(page).getByText(`整理发布说明 ${run}`).first().waitFor()
  step('任务详情：执行记录显示暂停的一段，“在时间轴中查看”打开并选中这个任务')

  // ---------------------------------------------------------------- viewer 只读
  const viewer = await login(browser, viewerEmail, problems)
  await viewer.page.getByLabel('当前工作空间').selectOption({ label: 'TlOwner 的空间' })
  await openTimeline(viewer.page, { date: humanDay, project: projectA.id })
  await clip(viewer.page, `整理发布说明 ${run}`).click()
  await detail(viewer.page).getByText(`整理发布说明 ${run}`).first().waitFor()
  for (const name of ['开始', '暂停', '交给它执行', '结束这段']) {
    check(await detail(viewer.page).getByRole('button', { name }).count() === 0, `viewer 不应看到“${name}”`)
  }
  await openTimeline(viewer.page, { date: dayOf(Date.parse(moved.planned_start_at)), project: projectB.id })
  const viewerPlanned = clip(viewer.page, `更新巡检脚本 ${run}`)
  await viewerPlanned.waitFor()
  check(!(await viewerPlanned.evaluate((el) => el.classList.contains('can-drag'))), 'viewer 不应能拖动计划片段')
  step('viewer：能看时间轴与详情，没有开始 / 暂停 / 交给它执行 / 结束这段，计划片段不能拖')

  check(problems.length === 0, `页面错误：${problems.join(' | ')}`)
  await browser.close()
  console.log('时间轴浏览器验收通过')
})().catch((error) => { console.error(error); process.exit(1) })
