// Sprint 4 浏览器验收（T-0032）：真实后端上的知识入口、日历与首页组件——
// 配置 AFFiNE 地址 → 新增收藏并关联任务 → 任务抽屉显示相关文档 → 搜索命中 → 日历新建 / 编辑 →
// 首页“最近文档”“近期日程”显示真实数据 → viewer 只读 → 删除日程与收藏后搜索不再出现。
// GitHub 连接、开发者页与全局搜索框由 T-0031 的前端提供，合并后在本脚本里补上。
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

  // ---------------------------------------------------------------- 搜索命中（S4-03；搜索框由 T-0031 提供）
  check((await searchTitles(page, workspace, run)).includes(title), '搜索应能找到新收藏')
  step('跨资源搜索能找到新收藏')

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
  step('viewer：能看收藏与日程，没有新增、管理、删除入口，日程表单只读')

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
  step('A10：删除日程与收藏后，页面与搜索都不再出现')

  // ---------------------------------------------------------------- DoD：不请求 AFFiNE
  const affine = requests.filter((url) => url.startsWith(AFFINE))
  check(affine.length === 0, `浏览器不应请求 AFFiNE：${affine.join(', ')}`)
  step('DoD：全程没有向 AFFiNE 地址发出请求')

  await browser.close()
  if (problems.length) throw new Error(`页面错误：${problems.join('; ')}`)
  console.log('\n全部通过（页面无错误）')
})().catch((error) => {
  console.error(`失败  ${error.message}`)
  process.exit(1)
})
