// 项目与任务的浏览器验收（T-0082 起：工作台自己的项目与任务，之前是 T-0023 的 Vikunja 验收）——
// 新建项目 → 新建任务（带 Idempotency-Key、标签、今天截止）→ 编辑并标为完成 → 看板分列 → 首页待办汇总 →
// 删除任务 → 改名 / 归档 / 删除项目（连带任务）→ 移动端 → viewer 只读。
//
// 前提：Compose 全栈已启动并迁移（make up && make migrate）。
// 不要求空库：每次用新的工作台用户；用户由同目录的 seed.py 写入。
// 用法（依赖由同目录的 package.json / package-lock.json 锁定为 Playwright 1.63.0）：
//   cd tests/e2e && npm ci && npm run browser:install    # 首次：安装依赖与 Chromium
//   node sprint3_browser.cjs [BASE_URL]                    # 默认 http://127.0.0.1:${HTTP_PORT:-8080}
// CI（.github/workflows/ci-infra.yml）与本机共用。
const { execFileSync } = require('node:child_process')
const { randomBytes } = require('node:crypto')
const path = require('node:path')
const { chromium } = require('playwright')

const base = (process.argv[2] || `http://127.0.0.1:${process.env.HTTP_PORT || '8080'}`).replace(/\/$/, '')
const password = 'e2e sprint3 browser pass'
const run = randomBytes(4).toString('hex')

function seed(...args) {
  return execFileSync('python3', [path.join(__dirname, 'seed.py'), ...args], {
    encoding: 'utf8',
    env: { ...process.env, E2E_PASSWORD: password },
  }).trim().split(/\s+/)
}
function check(condition, message) { if (!condition) throw new Error(message) }

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
const taskButton = (page, title) => page.locator('button.task-title', { hasText: title })
const projectCard = (page, title) => page.getByRole('region', { name: `项目 ${title}` })

;(async () => {
  const [, workspace] = seed('user', `owner-${run}@browser.example.com`, 'Owner')
  const [viewerId] = seed('user', `viewer-${run}@browser.example.com`, 'Viewer')
  seed('member', workspace, viewerId, 'viewer')

  const browser = await chromium.launch()
  const problems = []
  const step = (text) => console.log(`通过  ${text}`)
  const { page } = await login(browser, `owner-${run}@browser.example.com`, problems)

  // ---------------------------------------------------------------- 新建项目
  const projectTitle = `浏览器验收 ${run}`
  await page.goto(`${base}/projects`)
  await page.getByRole('button', { name: '新建项目' }).click()
  await page.getByLabel('项目名称').fill(projectTitle)
  await page.getByLabel('项目说明').fill('浏览器验收创建')
  await page.getByRole('button', { name: '创建项目' }).click()
  await projectCard(page, projectTitle).waitFor()
  check(!(await page.content()).includes('Vikunja'), '项目页不应再出现 Vikunja')
  step('项目页新建项目，列表随即显示，页面里没有 Vikunja')

  // ---------------------------------------------------------------- 新建任务（带 Idempotency-Key）
  const keys = []
  page.on('request', (request) => {
    if (request.url().endsWith('/api/v1/tasks') && request.method() === 'POST') keys.push(request.headers()['idempotency-key'])
  })
  async function createTask(title, { labels = '', dueToday = false } = {}) {
    await page.getByRole('button', { name: '新建任务' }).click()
    await page.getByLabel('任务标题').fill(title)
    await page.getByLabel('任务项目').selectOption({ label: projectTitle })
    await page.getByLabel('任务优先级').selectOption({ index: 3 })
    if (labels) await page.getByLabel('任务标签').fill(labels)
    if (dueToday) {
      // 截止时间设为今天，首页“今日任务”应显示它
      const soon = new Date(Date.now() + 60 * 60 * 1000)
      const local = new Date(soon.getTime() - soon.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
      await page.getByLabel('截止时间', { exact: true }).fill(local)
    }
    await page.getByRole('button', { name: '创建任务' }).click()
    await taskButton(page, title).waitFor()
  }
  await page.goto(`${base}/tasks`)
  await createTask('浏览器新建', { labels: '验收，浏览器' })
  await createTask('一直未完成', { dueToday: true })
  check(keys.length === 2 && keys.every((key) => /^[0-9a-f-]{36}$/.test(key)) && keys[0] !== keys[1], '每次创建应带新的 UUID 格式 Idempotency-Key')
  check(await taskButton(page, '浏览器新建').count() === 1, '新建的任务应只出现一次')
  await page.getByText(`${projectTitle} · 验收 · 浏览器`).first().waitFor()
  step('新建任务抽屉：每次提交带新的 Idempotency-Key，列表显示所属项目与标签')

  // ---------------------------------------------------------------- 编辑（带 version）
  await taskButton(page, '浏览器新建').click()
  await page.getByLabel('任务标题').fill('浏览器修改')
  await page.getByLabel('已完成任务').check()
  await page.getByRole('button', { name: '保存任务' }).click()
  await taskButton(page, '浏览器修改').waitFor()
  await page.getByRole('button', { name: '关闭对话框' }).click().catch(() => {})
  step('编辑抽屉：修改标题并标为完成')

  // ---------------------------------------------------------------- 看板
  await page.getByRole('button', { name: '看板', exact: true }).click()
  await page.getByRole('region', { name: '未完成' }).locator('button.task-title', { hasText: '一直未完成' }).waitFor()
  await page.getByRole('region', { name: '已完成' }).locator('button.task-title', { hasText: '浏览器修改' }).waitFor()
  step('看板视图按完成状态分列')

  // ---------------------------------------------------------------- 首页任务组件
  await page.goto(`${base}/`)
  const summary = page.getByRole('article', { name: '待办汇总' })
  await summary.locator('.summary-number').waitFor()
  check((await summary.locator('.summary-number').textContent()).trim() === '1', '待办汇总应为 1（只有一个未完成）')
  step('首页待办汇总显示工作台自己的任务数据')

  // ---------------------------------------------------------------- 删除任务（二次确认）
  await page.goto(`${base}/tasks`)
  await page.getByRole('button', { name: '列表', exact: true }).click()
  await taskButton(page, '浏览器修改').click()
  await page.getByRole('button', { name: '删除任务' }).click()
  await page.getByText('任务操作已完成').first().waitFor()
  await page.getByRole('button', { name: '刷新任务' }).click()
  await taskButton(page, '一直未完成').waitFor()
  check(await taskButton(page, '浏览器修改').count() === 0, '删除后任务应从列表消失')
  step('删除任务（确认后）从列表消失')

  // ---------------------------------------------------------------- 项目改名、归档、删除
  await page.goto(`${base}/projects`)
  await projectCard(page, projectTitle).getByRole('button', { name: `编辑项目 ${projectTitle}` }).click()
  const renamed = `${projectTitle} 改名`
  await page.getByLabel('项目名称').fill(renamed)
  await page.getByRole('button', { name: '保存项目' }).click()
  await projectCard(page, renamed).waitFor()
  await projectCard(page, renamed).getByRole('button', { name: `归档项目 ${renamed}` }).click()
  await page.getByText(`已归档项目「${renamed}」，任务保留`).waitFor()
  check(await projectCard(page, renamed).count() === 0, '归档的项目默认不显示')
  await page.getByLabel('显示已归档的项目').check()
  await projectCard(page, renamed).getByText('已归档').waitFor()
  await projectCard(page, renamed).getByRole('button', { name: `删除项目 ${renamed}` }).click()
  await page.getByText(`已删除项目「${renamed}」`).waitFor()
  await page.goto(`${base}/tasks`)
  await page.getByText('还没有项目，请先在「项目」页新建一个项目。').first().waitFor()
  check(await taskButton(page, '一直未完成').count() === 0, '删除项目应一起删除它的任务')
  step('项目改名、归档（默认隐藏）、删除（连带任务）')

  // ---------------------------------------------------------------- 移动端
  await page.goto(`${base}/projects`)
  await page.getByRole('button', { name: '新建项目' }).click()
  await page.getByLabel('项目名称').fill(projectTitle)
  await page.getByRole('button', { name: '创建项目' }).click()
  await projectCard(page, projectTitle).waitFor()
  await page.goto(`${base}/tasks`)
  await createTask('移动端检查')
  await page.setViewportSize({ width: 390, height: 844 })
  await taskButton(page, '移动端检查').waitFor()
  check(!(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)), '移动端任务页不应出现横向滚动')
  await page.goto(`${base}/projects`)
  await projectCard(page, projectTitle).waitFor()
  check(!(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)), '移动端项目页不应出现横向滚动')
  step('390px 宽度下任务页、项目页无横向滚动')

  // ---------------------------------------------------------------- viewer 只读
  const viewer = await login(browser, `viewer-${run}@browser.example.com`, problems)
  await viewer.page.getByLabel('当前工作空间').selectOption({ label: 'Owner 的空间' })
  await viewer.page.goto(`${base}/tasks`)
  await taskButton(viewer.page, '移动端检查').waitFor()
  check(await viewer.page.getByRole('button', { name: '新建任务' }).count() === 0, 'viewer 不应看到新建任务')
  await taskButton(viewer.page, '移动端检查').click()
  check(await viewer.page.getByRole('button', { name: '保存任务' }).count() === 0, 'viewer 不应看到保存任务')
  check(await viewer.page.getByRole('button', { name: '删除任务' }).count() === 0, 'viewer 不应看到删除任务')
  await viewer.page.goto(`${base}/projects`)
  await projectCard(viewer.page, projectTitle).waitFor()
  check(await viewer.page.getByRole('button', { name: '新建项目' }).count() === 0, 'viewer 不应看到新建项目')
  check(await projectCard(viewer.page, projectTitle).getByRole('button').count() === 0, 'viewer 不应看到项目的编辑、归档、删除')
  step('viewer：能看任务和项目，没有新建、保存、删除入口')

  await browser.close()
  if (problems.length) throw new Error(`页面错误：${problems.join('; ')}`)
  console.log('\n全部通过（页面无错误）')
})().catch((error) => {
  console.error(`失败  ${error.message}`)
  process.exit(1)
})
