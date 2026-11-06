// 项目 README 与 Issues 浏览器验收（T-0125；方案 docs/v2/AGENT_MESSAGING_PLAN.md 第 8、9 节）：
// 真实后端上的项目页——README（自动更新的部分带角标）→ Issues 标签 → 页面上开一个“接口约定”Issue →
// README 的沟通部分自动列出它（Worker 每 20 秒刷新）→ 评论 → 关闭 → 沟通部分改为“没有未关闭的” →
// 手机宽度不横向滚动 → viewer 只读（看得到，不能开 Issue、不能评论）。
//
// 前提：Compose 全栈已启动并迁移（make up && make migrate）。
// 不要求空库：每次用新的工作台用户；用户与成员由同目录的 seed.py 写入。
// 智能体经 MCP 读写 README、评论 Issue 的部分在 agents_acceptance.py 场景 25（真实 OpenClaw）。
// 用法（依赖由同目录的 package.json / package-lock.json 锁定）：
//   cd tests/e2e && npm ci && npm run browser:install    # 首次：安装依赖与 Chromium
//   node readme_issues_browser.cjs [BASE_URL]              # 默认 http://127.0.0.1:${HTTP_PORT:-8080}
// CI（.github/workflows/ci-infra.yml）与本机共用。
const { execFileSync } = require('node:child_process')
const { randomBytes, randomUUID } = require('node:crypto')
const path = require('node:path')
const { chromium } = require('playwright')

const base = (process.argv[2] || `http://127.0.0.1:${process.env.HTTP_PORT || '8080'}`).replace(/\/$/, '')
const password = 'e2e readme issues pass'
const run = randomBytes(4).toString('hex')
const ownerEmail = `ri-owner-${run}@browser.example.com`
const viewerEmail = `ri-viewer-${run}@browser.example.com`

function seed(...args) {
  return execFileSync('python3', [path.join(__dirname, 'seed.py'), ...args], {
    encoding: 'utf8',
    env: { ...process.env, E2E_PASSWORD: password },
  }).trim().split(/\s+/)
}
function check(condition, message) { if (!condition) throw new Error(message) }
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

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
  const context = await browser.newContext({ viewport, locale: 'zh-CN' })
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
const readme = (page) => page.getByRole('region', { name: 'README' })
// README 由 Worker 每 20 秒刷新：重新打开项目页，直到 README 里出现期望的文字
async function waitReadme(page, projectId, text, present = true, timeout = 120_000) {
  const until = Date.now() + timeout
  for (;;) {
    await page.goto(`${base}/projects/${projectId}`)
    await readme(page).waitFor()
    const content = await readme(page).innerText()
    if (content.includes(text) === present) return content
    check(Date.now() < until, `README ${present ? '没有' : '仍然有'}“${text}”：${content.slice(0, 400)}`)
    await sleep(5000)
  }
}

;(async () => {
  const [, workspace] = seed('user', ownerEmail, 'RiOwner')
  const [viewerId] = seed('user', viewerEmail, 'RiViewer')
  seed('member', workspace, viewerId, 'viewer')

  const browser = await chromium.launch()
  const problems = []
  const step = (text) => console.log(`通过  ${text}`)
  const { context, page } = await login(browser, ownerEmail, problems)

  // ---------------------------------------------------------------- README：打开项目先看 README，自动更新的部分带角标
  const project = await api(page, workspace, 'POST', '/api/v1/projects', { title: `README 验收 ${run}`, description: '浏览器验收' }, { 'Idempotency-Key': randomUUID() })
  await page.goto(`${base}/projects/${project.id}`)
  await page.getByRole('heading', { level: 1, name: `README 验收 ${run}` }).waitFor()
  await readme(page).waitFor()
  check(await page.getByRole('group', { name: '项目' }).getByRole('button', { name: 'README' }).getAttribute('aria-pressed') === 'true', '打开项目应先显示 README')
  const autoParts = await readme(page).locator('[aria-label^="自动更新："]').count()
  check(autoParts >= 2, `README 应有工作台自动更新的部分：${autoParts}`)
  const noIssues = await waitReadme(page, project.id, '没有未关闭的')
  check(noIssues.includes('沟通'), 'README 应有“沟通”部分')
  step(`README：打开项目先显示 README，${autoParts} 个部分带“自动更新”角标，沟通部分写着没有未关闭的 Issue`)

  // ---------------------------------------------------------------- Issues：页面上开一个接口约定
  await page.getByRole('group', { name: '项目' }).getByRole('button', { name: 'Issues' }).click()
  await page.waitForURL(/tab=issues/)
  const issues = page.getByRole('region', { name: 'Issues' })
  await issues.getByRole('button', { name: '新建 Issue' }).click()
  const form = page.getByRole('form', { name: '新建 Issue' })
  await form.getByLabel('接口约定').check()
  await form.getByLabel('标题').fill(`导出接口字段 ${run}`)
  await form.getByLabel(/^正文/).fill('字段：`id`、`title`、`done_at`。请确认。')
  await form.getByRole('button', { name: '开 Issue' }).click()
  await page.waitForURL(/\/issues\/[0-9a-f-]{36}$/)
  const issueId = page.url().split('/').pop()
  const article = page.getByRole('article', { name: /^Issue #\d+$/ })
  await article.waitFor()
  const number = (await article.getAttribute('aria-label')).replace('Issue #', '')
  check(await article.getByRole('region', { name: '正文' }).getByText('done_at').count() > 0, 'Issue 正文应渲染出来')
  step(`Issues：项目页切到 Issues，页面上开了“接口约定” #${number}，打开详情`)

  // README 的沟通部分自动列出这个 Issue
  const listed = await waitReadme(page, project.id, `导出接口字段 ${run}`)
  check(listed.includes(`#${number}`), `README 应写出 Issue 编号：${listed.slice(0, 400)}`)
  step('README：Worker 刷新后沟通部分列出这个 Issue（编号与标题）')

  // ---------------------------------------------------------------- 评论与关闭
  await page.goto(`${base}/issues/${issueId}`)
  await article.waitFor()
  const comment = page.getByRole('form', { name: '评论' })
  await comment.getByLabel(/^评论/).fill(`字段确认，按 ${run} 版本实现。`)
  await comment.getByRole('button', { name: '评论' }).click()
  await page.getByRole('list', { name: '时间线' }).getByText(`按 ${run} 版本实现`).waitFor()
  const closer = page.getByRole('form', { name: '关闭 Issue' })
  await closer.getByLabel('关闭原因').selectOption('resolved')
  await closer.getByRole('button', { name: '关闭' }).click()
  await page.getByRole('form', { name: '关闭 Issue' }).waitFor({ state: 'detached' })
  const detail = await api(page, workspace, 'GET', `/api/v1/issues/${issueId}`)
  check(detail.state === 'closed' && detail.close_reason === 'resolved' && detail.comments.length === 1, `Issue 应已关闭并有一条评论：${JSON.stringify({ state: detail.state, reason: detail.close_reason, comments: detail.comments.length })}`)
  step('Issue：评论出现在时间线里；按“已解决”关闭')

  await waitReadme(page, project.id, `导出接口字段 ${run}`, false)
  step('README：关闭后沟通部分不再列出它')

  await page.goto(`${base}/projects/${project.id}?tab=issues`)
  await issues.waitFor()
  await issues.getByRole('button', { name: /^已关闭/ }).click()
  await issues.getByRole('link', { name: new RegExp(`#${number} 导出接口字段 ${run}`) }).waitFor()
  step('Issues：已关闭的列表里找得到它')

  // ---------------------------------------------------------------- 手机宽度
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`${base}/projects/${project.id}`)
  await readme(page).waitFor()
  check(await noHorizontalScroll(page), '手机宽度的项目页（README）不应横向滚动')
  await page.goto(`${base}/issues/${issueId}`)
  await article.waitFor()
  check(await noHorizontalScroll(page), '手机宽度的 Issue 页不应横向滚动')
  step('手机宽度：项目 README 与 Issue 页都不横向滚动')
  await context.close()

  // ---------------------------------------------------------------- viewer 只读
  const viewer = await login(browser, viewerEmail, problems)
  await viewer.page.goto(`${base}/projects/${project.id}?tab=issues`)
  const viewerIssues = viewer.page.getByRole('region', { name: 'Issues' })
  await viewerIssues.waitFor()
  await viewer.page.waitForTimeout(500)
  check(await viewerIssues.getByRole('button', { name: '新建 Issue' }).count() === 0, 'viewer 不应看到“新建 Issue”')
  await viewer.page.goto(`${base}/issues/${issueId}`)
  await viewer.page.getByRole('article', { name: `Issue #${number}` }).waitFor()
  check(await viewer.page.getByRole('form', { name: '评论' }).count() === 0, 'viewer 不应看到评论框')
  check(await viewer.page.getByRole('list', { name: '时间线' }).getByText(`按 ${run} 版本实现`).count() === 1, 'viewer 应看得到评论')
  step('viewer：看得到 Issue 和评论，不能开 Issue、不能评论')
  await viewer.context.close()

  await browser.close()
  check(problems.length === 0, `页面出现错误：\n${problems.join('\n')}`)
  console.log('README 与 Issues 浏览器验收全部通过')
})().catch((error) => {
  console.error(error)
  process.exit(1)
})
