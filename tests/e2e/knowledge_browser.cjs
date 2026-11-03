// 知识库浏览器验收（T-0100，方案 docs/v2/KNOWLEDGE_PLAN.md 第 6 节 K-6）：真实后端上的独立知识库页面——
// 知识图谱点节点 → 编辑器 [[ 补全、保存后链接解析、行内 #标签 → 两个标签页同时编辑，三方合并后保存 →
// 删除进回收站再恢复 → 项目知识库主页（简报、每周修改的趋势图与悬停提示）→ 手机宽度 → viewer 只读。
//
// 前提：Compose 全栈已启动并迁移（make up && make migrate）。
// 不要求空库：每次用新的工作台用户；用户由同目录的 seed.py 写入，笔记与项目经工作台接口建。
// 用法（依赖由同目录的 package.json / package-lock.json 锁定）：
//   cd tests/e2e && npm ci && npm run browser:install    # 首次：安装依赖与 Chromium
//   node knowledge_browser.cjs [BASE_URL]                  # 默认 http://127.0.0.1:${HTTP_PORT:-8080}
// CI（.github/workflows/ci-infra.yml）与本机共用。
const { execFileSync } = require('node:child_process')
const { randomBytes, randomUUID } = require('node:crypto')
const path = require('node:path')
const { chromium } = require('playwright')

const base = (process.argv[2] || `http://127.0.0.1:${process.env.HTTP_PORT || '8080'}`).replace(/\/$/, '')
const password = 'e2e knowledge browser pass'
const run = randomBytes(4).toString('hex')
const ownerEmail = `kb-owner-${run}@browser.example.com`
const viewerEmail = `kb-viewer-${run}@browser.example.com`

function seed(...args) {
  return execFileSync('python3', [path.join(__dirname, 'seed.py'), ...args], {
    encoding: 'utf8',
    env: { ...process.env, E2E_PASSWORD: password },
  }).trim().split(/\s+/)
}
function check(condition, message) { if (!condition) throw new Error(message) }

// 页面里直接调用接口（带会话 Cookie、CSRF 与工作空间），用于准备数据
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

function watch(page, problems) {
  page.on('pageerror', (error) => problems.push(error.message))
  page.on('console', (message) => {
    // 登录前查询当前用户的 401、故意制造的版本冲突（409）是预期响应，浏览器会把它们记为资源加载失败
    if (message.type() === 'error' && !/status of (401|409)\b/.test(message.text())) problems.push(message.text())
  })
  page.on('dialog', (dialog) => dialog.accept())
}
async function login(browser, email, problems) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN', timezoneId: 'Asia/Shanghai' })
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
const editorReady = (page) => page.waitForFunction(() => document.querySelector('.cm-content'))
const saveButton = (page) => page.getByRole('button', { name: '保存', exact: true }).last()
const NOTE_URL = /\/knowledge\/notes\/[0-9a-f-]{36}$/

;(async () => {
  const [, workspace] = seed('user', ownerEmail, 'KbOwner')
  const [viewerId] = seed('user', viewerEmail, 'KbViewer')
  seed('member', workspace, viewerId, 'viewer')

  const browser = await chromium.launch()
  const problems = []
  const step = (text) => console.log(`通过  ${text}`)
  const { context, page } = await login(browser, ownerEmail, problems)

  // ---------------------------------------------------------------- 准备：公共库的流程笔记、一个项目（项目库自动建）与简报
  const spaces = (await api(page, workspace, 'GET', '/api/v1/knowledge/spaces')).items
  const pub = spaces.find((s) => s.kind === 'public')
  check(pub, '新工作空间应自动有公共知识库')
  const project = await api(page, workspace, 'POST', '/api/v1/projects', { title: `知识库浏览器 ${run}`, description: '' }, { 'Idempotency-Key': randomUUID() })
  const projectSpace = (await api(page, workspace, 'GET', '/api/v1/knowledge/spaces')).items.find((s) => s.project_id === project.id)
  check(projectSpace, '新项目应自动有项目知识库')
  const flowTitle = `发布流程 ${run}`
  const flow = await api(page, workspace, 'POST', '/api/v1/knowledge/notes', { space_id: pub.id, title: flowTitle, body: '1. 备份\n2. 迁移\n3. 发布', type: 'process' })
  await api(page, workspace, 'POST', '/api/v1/knowledge/notes', { space_id: projectSpace.id, title: `知识库浏览器 ${run} 简报`, body: `目标：浏览器验收。流程见 [[${flowTitle}]]`, type: 'brief' })

  // ---------------------------------------------------------------- 独立页面与知识图谱
  await page.goto(`${base}/knowledge`)
  await page.getByRole('navigation', { name: '知识库功能' }).waitFor()
  check(await page.getByRole('navigation', { name: '主导航' }).count() === 0, '知识库是独立页面，不应显示工作台的主导航')
  const node = page.getByRole('button', { name: new RegExp(`^${flowTitle}，笔记`) })
  await node.waitFor()
  await node.click()
  const selected = page.getByRole('complementary', { name: '所选笔记' })
  check((await selected.getByRole('link', { name: '打开笔记' }).getAttribute('href')) === `/knowledge/notes/${flow.id}`, '所选笔记的“打开笔记”应指向这篇笔记')
  step('独立页面 /knowledge：功能条与知识图谱，点节点显示所选笔记')

  // ---------------------------------------------------------------- 编辑器：[[ 补全、保存、链接解析、行内 #标签
  const mineTitle = `浏览器写的笔记 ${run}`
  await page.goto(`${base}/knowledge/notes/new?space=${pub.id}`)
  const title = page.getByRole('textbox', { name: '标题' })
  await title.waitFor()
  await editorReady(page)
  await title.fill(mineTitle)
  await page.locator('.cm-content').click()
  await page.keyboard.type('流程见 [[发布流程')
  const option = page.locator('.cm-tooltip-autocomplete li', { hasText: flowTitle })
  await option.first().waitFor({ timeout: 10000 })
  await option.first().click()
  check((await page.locator('.cm-content').innerText()).includes(`[[${flowTitle}]]`), `选中补全后应插入 [[${flowTitle}]]`)
  await page.keyboard.type(' 和 #验收 ')
  await saveButton(page).click()
  await page.waitForURL(NOTE_URL)
  const mineUrl = page.url()
  const link = page.getByRole('article').getByRole('link', { name: flowTitle })
  await link.waitFor()
  check((await link.getAttribute('href')) === `/knowledge/notes/${flow.id}`, '保存后 [[链接]] 应解析为那篇笔记的站内链接')
  check(await page.getByRole('link', { name: '#验收' }).count() > 0, '行内 #验收 应并入标签')
  step('编辑器：[[ 补全、保存后链接解析为站内链接、行内 #标签')

  // ---------------------------------------------------------------- 两个标签页同时编辑：提示版本冲突，三方合并保留两边的改动
  const other = await context.newPage()
  watch(other, problems)
  await other.goto(`${mineUrl}/edit`)
  await editorReady(other)
  await page.goto(`${mineUrl}/edit`)
  await editorReady(page)
  await other.locator('.cm-content').click()
  await other.keyboard.press('Control+End')
  await other.keyboard.type('\n另一处加的一行')
  await saveButton(other).click()
  await other.waitForURL(NOTE_URL)
  await other.close()
  await page.locator('.cm-content').click()
  await page.keyboard.press('Control+Home')
  await page.keyboard.type('开头加的一行\n')
  await saveButton(page).click()
  await page.getByText(/保存了第 2 版/).waitFor()
  await page.getByRole('button', { name: '合并它的改动' }).click()
  await page.getByText(/已合并第 2 版的改动/).waitFor()
  const merged = await page.locator('.cm-content').innerText()
  check(merged.includes('开头加的一行') && merged.includes('另一处加的一行'), '三方合并应保留两边的改动')
  await saveButton(page).click()
  await page.waitForURL(NOTE_URL)
  const saved = await page.getByRole('article').innerText()
  check(saved.includes('开头加的一行') && saved.includes('另一处加的一行'), '合并后应保存成功')
  step('版本冲突：另一个标签页先保存时提示，三方合并后保存（第 3 版）')

  // ---------------------------------------------------------------- 删除进回收站，在变更记录里恢复
  await page.getByRole('button', { name: '更多操作' }).click()
  await page.getByRole('menuitem', { name: /删除/ }).click()
  const dialog = page.getByRole('dialog', { name: '删除笔记' })
  await dialog.getByLabel(/原因/).fill('浏览器验收：删除再恢复')
  await dialog.getByRole('button', { name: '放进回收站' }).click()
  await page.waitForURL(/\/knowledge\/spaces\//)
  await page.goto(`${base}/knowledge/changes?tab=trash`)
  await page.getByRole('listitem').filter({ hasText: mineTitle }).getByRole('button', { name: '恢复' }).click()
  await page.getByText(`已恢复《${mineTitle}》`).waitFor()
  await page.goto(mineUrl)
  await page.getByRole('article').waitFor()
  check(await page.getByRole('button', { name: '恢复' }).count() === 0, '恢复后笔记不应还显示在回收站里')
  step('删除进回收站，在变更记录的回收站里恢复')

  // ---------------------------------------------------------------- 项目知识库主页：简报、每周修改的趋势图
  await page.goto(`${base}/knowledge/spaces/${projectSpace.id}`)
  await page.getByRole('region', { name: '项目简报' }).getByText('目标：浏览器验收').waitFor()
  const chart = page.getByRole('region', { name: '每周的修改' })
  const thisWeek = chart.getByRole('listitem').last()
  await thisWeek.waitFor()
  const described = await thisWeek.getAttribute('aria-label')
  check(/人 1 次，智能体 0 次$/.test(described || ''), `本周应有人的 1 次修改（简报）：${described}`)
  await thisWeek.hover()
  await chart.getByRole('status').filter({ hasText: '合计' }).waitFor()
  await chart.getByText('以表格查看').click()
  await chart.getByRole('table').waitFor()
  check(await chart.getByRole('row').count() === 9, '趋势图的表格视图应有 8 周（加表头）')
  step('项目知识库主页：简报、每周修改的趋势图（悬停提示、表格视图）')

  // ---------------------------------------------------------------- 手机宽度：底部导航，不横向滚动
  const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, locale: 'zh-CN', storageState: await context.storageState() })
  const phone = await mobile.newPage()
  watch(phone, problems)
  for (const url of ['/knowledge', `/knowledge/notes/${flow.id}`, `/knowledge/spaces/${projectSpace.id}`, '/knowledge/changes']) {
    await phone.goto(base + url)
    await phone.getByRole('navigation', { name: '知识库导航' }).waitFor()
    await phone.waitForLoadState('networkidle')
    check(await noHorizontalScroll(phone), `手机宽度下 ${url} 不应横向滚动`)
  }
  await mobile.close()
  step('手机宽度（390）：底部导航，各页面不横向滚动')

  // ---------------------------------------------------------------- viewer 只读
  const viewer = await login(browser, viewerEmail, problems)
  await viewer.page.getByLabel('当前工作空间').selectOption({ label: 'KbOwner 的空间' })
  await viewer.page.goto(`${base}/knowledge/notes/${flow.id}`)
  await viewer.page.getByRole('article').getByText('备份').waitFor()
  check(await viewer.page.getByRole('link', { name: '编辑' }).count() === 0, 'viewer 不应看到编辑')
  await viewer.page.goto(`${base}/knowledge/spaces/${projectSpace.id}`)
  await viewer.page.getByRole('region', { name: '项目简报' }).waitFor()
  check(await viewer.page.getByRole('link', { name: '新建笔记' }).count() === 0, 'viewer 不应看到新建笔记')
  check(await viewer.page.getByRole('link', { name: '编辑简报' }).count() === 0, 'viewer 不应看到编辑简报')
  step('viewer：能读笔记与项目知识库，看不到编辑与新建')

  check(problems.length === 0, `页面错误：${problems.join(' | ')}`)
  await browser.close()
  console.log('知识库浏览器验收通过')
})().catch((error) => { console.error(error); process.exit(1) })
