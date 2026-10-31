// Sprint 2 浏览器验收（T-0017）：真实后端上的首页仪表盘——拖拽保存刷新、版本冲突、组件增删、
// 损坏组件的错误卡片、viewer 只读、任务模块降级、本地搜索、移动端单列与暗色主题。
//
// 前提：Compose 全栈已启动并迁移（make up && make migrate）。不要求空库：测试用户每次用新的邮箱，
// 由同目录的 seed.py 经 `docker compose exec api` 写入（需要 python3 与 docker compose）。
// 用法（依赖由同目录的 package.json / package-lock.json 锁定为 Playwright 1.63.0）：
//   cd tests/e2e && npm ci && npm run browser:install    # 首次：安装依赖与 Chromium
//   node sprint2_browser.cjs [BASE_URL]                    # 默认 http://127.0.0.1:${HTTP_PORT:-8080}
//   - BASE_URL 的来源必须是 PUBLIC_BASE_URL 或在 CORS_ALLOWED_ORIGINS 里，否则写请求会被来源校验拒绝。
// CI（.github/workflows/ci-infra.yml）与本机共用。
const { execFileSync } = require('node:child_process')
const { randomBytes } = require('node:crypto')
const path = require('node:path')
const { chromium } = require('playwright')

const base = (process.argv[2] || `http://127.0.0.1:${process.env.HTTP_PORT || '8080'}`).replace(/\/$/, '')
const password = 'e2e sprint2 browser pass'
const run = randomBytes(4).toString('hex')
const STARTERS = ['文本便签', '常用链接', '待办汇总', '今日任务']

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
    // 登录前查询当前用户的 401、版本冲突的 409 是预期响应，浏览器会把它们记为资源加载失败
    if (message.type() === 'error' && !/\b(401|409)\b/.test(message.text())) problems.push(message.text())
  })
  page.on('dialog', (dialog) => dialog.accept())
  await page.goto(`${base}/login`)
  await page.getByLabel('邮箱').fill(email)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '登录' }).click()
  await page.waitForURL(`${base}/`)
  return { context, page }
}
// 页面上的桌面网格位置：{ 组件 ID: [x, y, w, h] }
function gridLayout(page) {
  return page.locator('.grid-stack-item').evaluateAll((items) => Object.fromEntries(items.map((item) =>
    [item.getAttribute('gs-id'), ['gs-x', 'gs-y', 'gs-w', 'gs-h'].map((name) => Number(item.getAttribute(name)))])))
}
// 用页面自己的会话读取首页聚合（与前端读取的是同一个接口）
function overview(page, workspaceId) {
  return page.evaluate(async (id) => (await fetch('/api/v1/dashboard/overview', { headers: { 'X-Workspace-Id': id } })).json(), workspaceId)
}

;(async () => {
  const [, workspace, dashboardId] = seed('user', `alice-${run}@browser.example.com`, 'Alice')
  const [victorId] = seed('user', `victor-${run}@browser.example.com`, 'Victor')
  seed('member', workspace, victorId, 'viewer')

  const browser = await chromium.launch()
  const problems = []
  const step = (text) => console.log(`通过  ${text}`)
  const { page } = await login(browser, `alice-${run}@browser.example.com`, problems)

  for (const title of STARTERS) await page.getByRole('article', { name: title }).waitFor()
  // 组件卡片与首页聚合的模块状态不一定同时到达：等两段降级说明都出现再计数（T-0048）
  const unavailable = page.getByText('任务模块暂不可用，尚未接入任务服务。')
  await unavailable.nth(1).waitFor()
  check(await unavailable.count() === 2, '两个任务组件应显示尚未接入')
  check(await page.locator('.summary-number').count() === 0, '汇总值未知时不应显示数字')
  step('首页显示 4 个入门组件；A05 任务模块不可用时任务组件显示降级说明、不显示 0，便签与链接正常')

  // ---------------------------------------------------------------- 拖拽 → 保存 → 刷新
  const before = await overview(page, workspace)
  const version = before.dashboard.version
  const note = before.widgets.find((widget) => widget.widget_type === 'note')
  const item = page.locator(`.grid-stack-item[gs-id="${note.id}"]`)
  await page.getByRole('button', { name: '编辑布局', exact: true }).click()
  const startY = await item.getAttribute('gs-y')
  const handle = await item.locator('.widget-handle').boundingBox()
  await page.mouse.move(handle.x + 30, handle.y + 10)
  await page.mouse.down()
  await page.mouse.move(handle.x + 30, handle.y + 600, { steps: 25 })
  await page.mouse.up()
  check(await item.getAttribute('gs-y') !== startY, '拖拽后便签的位置应改变')
  // GridStack 只在鼠标悬停时显示调整大小的把手
  await item.scrollIntoViewIfNeeded()
  await item.hover()
  const corner = await item.locator('.ui-resizable-se').boundingBox()
  check(corner, '编辑模式下应有调整大小的把手')
  const startW = await item.getAttribute('gs-w')
  await page.mouse.move(corner.x + corner.width / 2, corner.y + corner.height / 2)
  await page.mouse.down()
  await page.mouse.move(corner.x - 160, corner.y + 90, { steps: 12 })
  await page.mouse.up()
  check(await item.getAttribute('gs-w') !== startW, '拖动右下角后宽度应改变')
  await page.getByRole('button', { name: '保存布局', exact: true }).click()
  await page.getByText('布局已保存', { exact: true }).waitFor()
  const dragged = await gridLayout(page)
  const saved = await overview(page, workspace)
  check(saved.dashboard.version === version + 1, `保存后版本应 +1：${saved.dashboard.version}`)
  for (const widget of saved.widgets) {
    const { x, y, w, h } = widget.layout.desktop
    check(JSON.stringify(dragged[widget.id]) === JSON.stringify([x, y, w, h]), `后端保存的布局与页面不一致：${widget.widget_type}`)
  }
  // 首页按桌面端 (y, x) 排序，拖拽后顺序会变，按组件 ID 比较
  const mobile = (data) => JSON.stringify(Object.fromEntries(data.widgets.map((w) => [w.id, w.layout.mobile]).sort()))
  check(mobile(saved) === mobile(before), '桌面拖拽不应改动移动端顺序')
  step(`拖拽与调整大小 → 保存：后端保存的布局与页面一致，版本 ${version} → ${version + 1}，移动端顺序不变`)
  await page.reload()
  await page.getByText(`版本 ${version + 1}`, { exact: true }).waitFor()
  await page.getByRole('article', { name: '文本便签' }).waitFor()
  check(JSON.stringify(await gridLayout(page)) === JSON.stringify(dragged), '刷新后布局应与保存的一致')
  step('DoD：刷新后布局不丢（A04 页面级）')

  // ---------------------------------------------------------------- 版本冲突
  const other = await login(browser, `alice-${run}@browser.example.com`, problems)
  await page.getByRole('button', { name: '编辑布局', exact: true }).click()
  await other.page.getByRole('button', { name: '编辑布局', exact: true }).click()
  const otherNote = other.page.locator(`.grid-stack-item[gs-id="${note.id}"]`)
  await otherNote.getByText('键盘调整布局', { exact: true }).click()
  await otherNote.getByLabel('桌面宽度', { exact: true }).fill('5')
  await other.page.getByRole('button', { name: '保存布局', exact: true }).click()
  await other.page.getByText('布局已保存', { exact: true }).waitFor()
  await other.context.close()
  await item.getByText('键盘调整布局', { exact: true }).click()
  await item.getByLabel('桌面宽度', { exact: true }).fill('3')
  await page.getByRole('button', { name: '保存布局', exact: true }).click()
  await page.getByText('已被其他人修改，请重新加载最新版本后再操作。').waitFor()
  check(await page.getByRole('button', { name: '保存布局', exact: true }).isDisabled(), '冲突后应锁定保存')
  await page.getByRole('button', { name: '重新加载最新版本' }).click()
  await page.getByText(`版本 ${version + 2}`, { exact: true }).waitFor()
  check((await gridLayout(page))[note.id][2] === 5, '重新加载后应显示另一个会话保存的宽度')
  step('版本冲突：另一个会话先保存后，本页保存提示“已被其他人修改”并锁定；重新加载后显示最新布局，不覆盖对方')

  // ---------------------------------------------------------------- 组件增删
  await page.getByRole('button', { name: '添加组件', exact: true }).click()
  await page.getByLabel('组件配置 JSON').fill(JSON.stringify({ text: `浏览器添加的便签 ${run}` }))
  await page.getByRole('button', { name: '保存组件', exact: true }).click()
  await page.getByText('组件已添加', { exact: true }).waitFor()
  const added = page.locator('article.widget-card', { hasText: `浏览器添加的便签 ${run}` })
  await added.waitFor()
  await page.getByRole('button', { name: '编辑布局', exact: true }).click()
  await added.getByRole('button', { name: '删除组件' }).click()
  await page.getByText('组件已删除', { exact: true }).waitFor()
  check(await page.locator('article.widget-card').count() === 4, '删除后应剩 4 个组件')
  step('添加组件（便签按文本显示）并删除（确认后）')

  // ---------------------------------------------------------------- 本地搜索与主题
  await page.keyboard.press('Control+k')
  await page.getByRole('dialog', { name: '搜索当前工作空间' }).waitFor()
  await page.getByLabel('搜索页面与组件').fill('便签')
  await page.getByRole('button', { name: /文本便签/ }).waitFor()
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: '切换到暗色主题' }).click()
  check(await page.evaluate(() => document.documentElement.dataset.theme === 'dark'), '应切换到暗色主题')
  step('Ctrl+K 本地搜索能找到当前首页的组件；切换暗色主题')

  // ---------------------------------------------------------------- 损坏组件
  seed('corrupt', dashboardId)
  await page.reload()
  await page.getByRole('article', { name: '文本便签' }).waitFor()
  const broken = page.getByText('此组件类型或配置不受支持，其他组件仍可使用。')
  check(await broken.count() === 1, '损坏组件应显示安全的错误卡片')
  check(await page.locator('article.widget-card').count() === 5, '其余 4 个组件应正常显示')
  check(!(await page.content()).includes('not-an-object'), '不应渲染损坏组件的原始配置')
  const top = await broken.evaluate((element) => element.getBoundingClientRect().top + window.scrollY)
  check(top < 3000, `错误卡片应紧接在其余组件下方，实际在 ${Math.round(top)}px`)
  step('DoD：库里有已损坏的组件时只有它显示错误卡片，其余组件正常，页面不被撑高')

  // ---------------------------------------------------------------- 移动端单列
  await page.setViewportSize({ width: 390, height: 844 })
  await page.locator('.mobile-widget-grid').waitFor()
  check(await page.locator('.mobile-widget-grid > article').count() === 5, '移动端应单列显示全部组件')
  check(!(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)), '移动端不应出现横向滚动')
  step('390px 宽度下单列显示、无横向滚动（暗色主题）')

  // ---------------------------------------------------------------- viewer 只读（A03 页面级）
  const viewer = await login(browser, `victor-${run}@browser.example.com`, problems)
  await viewer.page.getByLabel('当前工作空间').selectOption({ label: 'Alice 的空间' })
  await viewer.page.getByRole('article', { name: '文本便签' }).waitFor()
  for (const name of ['编辑布局', '添加组件', '导入 JSON', '设为首页']) {
    check(await viewer.page.getByRole('button', { name, exact: true }).count() === 0, `viewer 不应看到“${name}”`)
  }
  step('viewer 切到别人的工作空间：能看首页，没有编辑、添加、导入、设为首页入口')

  await browser.close()
  if (problems.length) throw new Error(`页面错误：${problems.join('; ')}`)
  console.log('\n全部通过（页面无错误）')
})().catch((error) => {
  console.error(`失败  ${error.message}`)
  process.exit(1)
})
