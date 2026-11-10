// 应用中心浏览器验收（T-0212，方案 docs/v2/app-center/ AC-6）：真实后端 + 真实前端。
//
// 默认模式（compose job，没有部署 TrendRadar）：
//   汇总与模块卡片（声明能力、待部署）→ 打开详情（地址 ?module=news）四类状态分开 → 检测连接不出站（尚未配置）→
//   停用要确认、同步记为“无需同步”、操作记录有“停用模块” → 用旧 state_version 启停返回 409 → 再启用 →
//   页签方向键切换 → 自定义页面：添加、编辑时别人先保存（409）→ 确认合并 → 移除只改侧栏 →
//   admin 只读（可以检测）、member 打不开且接口 403 → 切换工作空间后显示新空间的状态 →
//   手机宽度不横向滚动、详情在抽屉里、Esc 关闭后焦点回到“查看详情” → 暗色主题。
// --deployed（news job，TrendRadar 已启动）：
//   检测连接成功 → 暂停 trendradar-mcp 后检测超时（down）→ 配置目录不可写时停用：开关已保存、同步失败 →
//   恢复后重试同步成功，配置文件是“不推送” → 再启用同步成功 → 关掉自己的早报（一个部署只能有一个工作空间启用）。
//
// 前提：Compose 全栈已启动并迁移（make up && make migrate；--deployed 还要 make trendradar-up）。
// 用法（依赖由同目录的 package.json / package-lock.json 锁定）：
//   cd tests/e2e && npm ci && npm run browser:install    # 首次：安装依赖与 Chromium
//   node app_center_browser.cjs [--deployed] [BASE_URL]   # 默认 http://127.0.0.1:${HTTP_PORT:-8080}
// SCREENSHOT_DIR 设了时保存桌面 / 手机 / 暗色截图。不要求空库：每次新建工作台用户。
const { execFileSync } = require('node:child_process')
const { randomBytes } = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require('playwright')

const ROOT = path.resolve(__dirname, '..', '..')
const args = process.argv.slice(2)
const deployed = args.includes('--deployed')
const base = (args.find((arg) => !arg.startsWith('--')) || `http://127.0.0.1:${process.env.HTTP_PORT || '8080'}`).replace(/\/$/, '')
const password = 'e2e app center browser pass'
const run = randomBytes(4).toString('hex')
const CONFIG_DIR = '/var/lib/workspace/trendradar'
const shots = process.env.SCREENSHOT_DIR || ''

function seed(...argv) {
  return execFileSync('python3', [path.join(__dirname, 'seed.py'), ...argv], {
    encoding: 'utf8',
    env: { ...process.env, E2E_PASSWORD: password },
  }).trim().split(/\s+/)
}
function check(condition, message) { if (!condition) throw new Error(message) }
const step = (text) => console.log(`通过  ${text}`)
const docker = (...argv) => execFileSync('docker', argv, { cwd: ROOT, encoding: 'utf8' }).trim()
const compose = (...argv) => docker('compose', ...argv)
const trendradar = (...argv) => execFileSync('docker', ['compose', '-p', 'my-workspace-trendradar', '-f', 'infra/trendradar/compose.yaml', '--env-file', '.env', ...argv], {
  cwd: ROOT, encoding: 'utf8', env: { ...process.env, WORKSPACE_PROJECT: process.env.WORKSPACE_PROJECT || 'my-workspace' },
}).trim()

async function api(page, workspace, method, pathname, body) {
  return page.evaluate(async ({ method, pathname, body, workspace }) => {
    const csrf = document.cookie.split('; ').find((item) => item.startsWith('ws_csrf='))?.slice(8)
    const headers = { 'X-Workspace-Id': workspace, ...(csrf ? { 'X-CSRF-Token': decodeURIComponent(csrf) } : {}) }
    if (body !== undefined) headers['Content-Type'] = 'application/json'
    const response = await fetch(pathname, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) })
    return { status: response.status, body: response.status === 204 ? null : await response.json() }
  }, { method, pathname, body, workspace })
}

function watch(page, problems) {
  page.on('pageerror', (error) => problems.push(error.message))
  page.on('response', (response) => {
    if (response.status() >= 500) problems.push(`${response.status()} ${response.request().method()} ${new URL(response.url()).pathname}`)
  })
  page.on('console', (message) => {
    // 预期内的拒绝（member 的 403、旧版本的 409、限频的 429）与外站链接的加载失败
    if (message.type() === 'error' && !/status of (401|403|404|409|429)\b|Failed to load resource|example\.com/.test(message.text())) problems.push(message.text())
  })
  page.on('dialog', (dialog) => { problems.push(`不应弹出浏览器对话框：${dialog.message()}`); void dialog.dismiss() })
}
async function login(browser, email, problems, options = {}) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN', ...options })
  const page = await context.newPage()
  watch(page, problems)
  await page.goto(`${base}/login`)
  await page.getByLabel('邮箱').fill(email)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '登录' }).click()
  await page.waitForURL(`${base}/`)
  return { context, page }
}
// seed.py 给每个用户都建了自己的空间：打开页面后切到要验收的工作空间
async function openAs(page, workspace, target) {
  await page.goto(`${base}${target}`)
  const select = page.getByLabel('当前工作空间')
  // 只有一个工作空间时没有选择框；手机宽度下它在折叠的菜单里（不可见）：直接改值并触发 change
  // 等页面标题出来（手机宽度下侧栏不在页面里），再看有没有工作空间选择框
  await page.getByRole('heading', { level: 1 }).first().waitFor()
  if (await select.count() === 0) return
  await select.waitFor({ state: 'attached' })
  if ((await select.inputValue()) === workspace) return
  if (await select.isVisible()) await select.selectOption(workspace)
  else await select.evaluate((element, value) => { element.value = value; element.dispatchEvent(new Event('change', { bubbles: true })) }, workspace)
}
async function noHorizontalScroll(page) {
  return !(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth))
}
async function shot(page, name) {
  if (!shots) return
  fs.mkdirSync(shots, { recursive: true })
  await page.screenshot({ path: path.join(shots, `${name}.png`), fullPage: true })
}
const sidebar = (page) => page.getByRole('navigation', { name: '主导航' })
async function setTabModules(page) {
  await page.getByRole('tablist', { name: '应用中心' }).getByRole('tab', { name: /功能模块/ }).click()
  await page.getByRole('article', { name: '模块：新闻早报' }).waitFor()
}
const card = (page) => page.getByRole('article', { name: '模块：新闻早报' })
const detail = (page) => page.getByRole('region', { name: '应用详情：新闻早报' })
const toggle = (page) => card(page).getByRole('switch', { name: '启用新闻早报' })
const fact = (page, name) => detail(page).getByRole('listitem').filter({ hasText: name })
async function switchTo(page, enabled) {
  await toggle(page).click()
  if (!enabled) await page.getByRole('dialog', { name: '停用应用' }).getByRole('button', { name: '停用应用' }).click()
  await page.waitForFunction((value) => document.querySelector('[role="switch"][aria-label="启用新闻早报"]')?.getAttribute('aria-checked') === value, String(enabled))
}
async function management(page, workspace) {
  const response = await api(page, workspace, 'GET', '/api/v1/modules/news/management')
  check(response.status === 200, `读取管理信息失败：${response.status}`)
  return response.body
}

async function composeMode(browser, problems) {
  const ownerEmail = `ac-owner-${run}@browser.example.com`, adminEmail = `ac-admin-${run}@browser.example.com`, memberEmail = `ac-member-${run}@browser.example.com`
  const [ownerId, workspace] = seed('user', ownerEmail, '应用中心验收')
  const [adminId] = seed('user', adminEmail, '应用中心管理员')
  const [memberId, memberSpace] = seed('user', memberEmail, '应用中心成员')
  seed('member', workspace, adminId, 'admin')
  seed('member', workspace, memberId, 'member')
  // 切换工作空间用：owner 在成员自己的空间里是 admin
  seed('member', memberSpace, ownerId, 'admin')

  const { page } = await login(browser, ownerEmail, problems)
  const probes = []
  page.on('request', (request) => { if (/\/health-check$/.test(new URL(request.url()).pathname)) probes.push(request.url()) })

  // ---------------------------------------------------------------- 汇总与卡片
  await openAs(page, workspace, '/settings/modules')
  await page.getByRole('heading', { name: '应用中心', level: 1 }).waitFor()
  await card(page).waitFor()
  const data = await api(page, workspace, 'GET', '/api/v1/modules/management')
  const tiles = page.getByRole('region', { name: '应用统计' })
  await tiles.locator('.ag-tile').filter({ hasText: new RegExp(`已接入模块\\s*${data.body.summary.registered}$`) }).waitFor()
  await tiles.locator('.ag-tile').filter({ hasText: new RegExp(`已启用\\s*${data.body.summary.enabled}$`) }).waitFor()
  const news = data.body.items.find((item) => item.module.key === 'news')
  check(news && news.module.ready === false, `compose job 没有部署 TrendRadar，新闻早报应是待部署：${JSON.stringify(news?.module)}`)
  await card(page).getByText(`${news.capabilities.tools.length} 个只读工具`).waitFor()
  await card(page).getByText('1 个定时任务').waitFor()
  await card(page).getByText('待部署').waitFor()
  await card(page).getByText('已启用此应用，完成部署配置后即可使用完整功能。').waitFor()
  check(probes.length === 0, '打开应用中心不应主动检测外部服务')
  await shot(page, 'app-center-desktop')
  step('汇总、模块卡片与声明能力；打开页面不探测外部服务')

  // ---------------------------------------------------------------- 详情：四类状态、检测连接（没部署不出站）
  await card(page).getByRole('button', { name: '查看详情：新闻早报' }).click()
  await page.waitForURL(/module=news/)
  await detail(page).waitFor()
  await fact(page, '工作空间开关').getByText('已启用').waitFor()
  await fact(page, '部署配置').getByText('待完成').waitFor()
  await fact(page, '服务连通性').getByText('尚未配置').waitFor()
  await detail(page).getByText('尚未配置', { exact: false }).first().waitFor()
  await detail(page).getByRole('button', { name: '检测连接' }).click()
  await page.getByText('「新闻早报」检测完成：尚未配置。').waitFor()
  check(probes.length === 1, '应只发出一次检测请求')
  step('详情四类状态分开；没部署时检测返回“尚未配置”')

  // ---------------------------------------------------------------- 停用（确认）、同步结果、操作记录、旧版本 409、再启用
  const before = await management(page, workspace)
  await switchTo(page, false)
  await page.getByText('「新闻早报」已停用。').waitFor()
  await fact(page, '外部配置同步').getByText('无需同步').waitFor()
  await fact(page, '外部配置同步').getByText('外部服务没有部署').waitFor()
  const after = await management(page, workspace)
  check(after.module.state_version === before.module.state_version + 1, `停用后 state_version 应加 1：${before.module.state_version} → ${after.module.state_version}`)
  await sidebar(page).getByRole('link', { name: '早报', exact: true }).waitFor({ state: 'detached' })
  await detail(page).getByRole('tab', { name: '操作记录' }).click()
  await detail(page).getByText('停用模块').first().waitFor()
  await detail(page).getByRole('tab', { name: '概览' }).click()
  const stale = await api(page, workspace, 'PATCH', '/api/v1/modules/news', { enabled: true, expected_state_version: before.module.state_version })
  check(stale.status === 409 && stale.body.error.code === 'VERSION_CONFLICT', `旧版本启停应返回 409：${JSON.stringify(stale)}`)
  check((await management(page, workspace)).module.enabled === false, '旧版本请求不应改动开关')
  await switchTo(page, true)
  await page.getByText('「新闻早报」已启用。').waitFor()
  await sidebar(page).getByRole('link', { name: '早报', exact: true }).waitFor()
  step('停用要确认、同步记为“无需同步”、操作记录；旧版本 409 不改开关；再启用恢复')

  // ---------------------------------------------------------------- 页签键盘
  const tablist = page.getByRole('tablist', { name: '应用中心' })
  await tablist.getByRole('tab', { name: /功能模块/ }).focus()
  await page.keyboard.press('ArrowRight')
  await page.waitForURL(/tab=pages/)
  check(await tablist.getByRole('tab', { name: /自定义页面/ }).evaluate((el) => el === document.activeElement), '方向键后焦点应在“自定义页面”页签')
  step('页签可以用方向键切换')

  // ---------------------------------------------------------------- 自定义页面：添加、冲突合并、移除
  const pagesPanel = page.getByRole('region', { name: '自定义页面' })
  await page.getByRole('button', { name: '添加入口' }).first().click()
  let dialog = page.getByRole('dialog', { name: '添加入口' })
  await dialog.getByLabel('名称').fill('验收入口')
  await dialog.getByLabel('链接地址').fill('https://docs.example.com/app-center')
  await dialog.getByRole('button', { name: '保存入口' }).click()
  await page.getByText('入口已保存。').waitFor()
  await pagesPanel.getByRole('listitem', { name: '入口：验收入口' }).waitFor()
  await sidebar(page).getByRole('link', { name: /验收入口/ }).waitFor()

  await pagesPanel.getByRole('button', { name: '编辑入口：验收入口' }).click()
  dialog = page.getByRole('dialog', { name: '编辑入口' })
  await dialog.getByRole('button', { name: '保存入口' }).waitFor()
  await dialog.getByLabel('名称').fill('验收入口二')
  // 另一个标签页先保存了布局（加了一个入口）
  const current = (await api(page, workspace, 'GET', '/api/v1/navigation')).body
  const elsewhere = await api(page, workspace, 'PUT', '/api/v1/navigation', {
    version: current.version, sections: current.sections, placements: current.placements, section_order: current.section_order,
    pages: [...current.pages, { id: `other${run}`, title: '别处添加', kind: 'link', section: 'apps', min_role: 'viewer', url: 'https://other.example.com', path: null, target_id: null }],
  })
  check(elsewhere.status === 200, `另一处保存失败：${elsewhere.status}`)
  await dialog.getByRole('button', { name: '保存入口' }).click()
  const conflict = page.getByRole('dialog', { name: '侧栏布局已被别人修改' })
  await conflict.getByText('你的修改还在，没有提交').waitFor()
  await shot(page, 'app-center-nav-conflict')
  await conflict.getByRole('button', { name: '合并后保存' }).click()
  await page.getByText('入口已保存。').waitFor()
  const merged = (await api(page, workspace, 'GET', '/api/v1/navigation')).body
  const titles = merged.pages.map((item) => item.title)
  check(titles.includes('验收入口二') && titles.includes('别处添加') && !titles.includes('验收入口'), `合并后应同时保留两处修改：${titles}`)

  await pagesPanel.getByRole('button', { name: '移除入口：验收入口二' }).click()
  dialog = page.getByRole('dialog', { name: '移除入口' })
  await dialog.getByText('不会删除它指向的笔记、仪表盘或外部网站').waitFor()
  await dialog.getByRole('button', { name: '移除入口' }).click()
  await page.getByText('入口已从侧栏移除（指向的内容没有删除）。').waitFor()
  await sidebar(page).getByRole('link', { name: /验收入口二/ }).waitFor({ state: 'detached' })
  step('自定义页面：添加、编辑时别人先保存 → 确认合并、移除只改侧栏')

  // ---------------------------------------------------------------- 角色
  const admin = await login(browser, adminEmail, problems)
  await openAs(admin.page, workspace, '/settings/modules?module=news')
  await card(admin.page).waitFor()
  check(await card(admin.page).getByRole('switch').count() === 0, 'admin 不应看到启用开关')
  check(await admin.page.getByRole('button', { name: '添加入口' }).count() === 0, 'admin 不应看到添加入口')
  await detail(admin.page).getByText('你可以查看；只有 owner 能启用、停用或重试同步。').waitFor()
  const adminRetry = await api(admin.page, workspace, 'POST', '/api/v1/modules/news/reconcile', { expected_state_version: 0 })
  check(adminRetry.status === 403, `admin 不能重试同步：${adminRetry.status}`)
  // member 在这个空间里看不到入口（整页打开会回到 member 自己的空间，所以不直接打开地址；路由守卫见前端测试）
  const member = await login(browser, memberEmail, problems)
  await openAs(member.page, workspace, '/')
  await sidebar(member.page).getByRole('link', { name: '任务', exact: true }).waitFor()
  check(await sidebar(member.page).getByRole('link', { name: '应用中心' }).count() === 0, 'member 不应看到应用中心入口')
  const memberRead = await api(member.page, workspace, 'GET', '/api/v1/modules/management')
  check(memberRead.status === 403, `member 读管理信息应 403：${memberRead.status}`)
  step('admin 只读（可以检测）；member 打不开应用中心，接口 403')

  // ---------------------------------------------------------------- 切换工作空间：页面按新空间重建
  await setTabModules(page)
  await switchTo(page, false)
  await page.getByLabel('当前工作空间').selectOption(memberSpace)
  await page.waitForFunction(() => !document.querySelector('[role="switch"][aria-label="启用新闻早报"]'))
  await card(page).getByText('已启用', { exact: true }).waitFor()
  await page.getByLabel('当前工作空间').selectOption(workspace)
  await page.waitForFunction(() => document.querySelector('[role="switch"][aria-label="启用新闻早报"]')?.getAttribute('aria-checked') === 'false')
  await switchTo(page, true)
  step('切换工作空间后显示新空间的状态与角色（另一个空间里是 admin、新闻早报默认启用）')

  // ---------------------------------------------------------------- 手机宽度、抽屉、Esc 与焦点、暗色
  const phone = await login(browser, ownerEmail, problems, { viewport: { width: 390, height: 844 } })
  await openAs(phone.page, workspace, '/settings/modules')
  await card(phone.page).waitFor()
  check(await noHorizontalScroll(phone.page), '应用中心在手机宽度横向滚动')
  await shot(phone.page, 'app-center-phone')
  const opener = card(phone.page).getByRole('button', { name: '查看详情：新闻早报' })
  await opener.click()
  await detail(phone.page).waitFor()
  check(await noHorizontalScroll(phone.page), '详情抽屉在手机宽度横向滚动')
  await phone.page.keyboard.press('Escape')
  await detail(phone.page).waitFor({ state: 'detached' })
  await phone.page.waitForFunction(() => document.activeElement?.getAttribute('aria-label') === '查看详情：新闻早报')
  step('手机宽度不横向滚动；详情在抽屉里，Esc 关闭后焦点回到“查看详情”')

  const dark = await login(browser, ownerEmail, problems, { colorScheme: 'dark' })
  await openAs(dark.page, workspace, '/settings/modules?module=news')
  await detail(dark.page).waitFor()
  const background = await dark.page.evaluate(() => getComputedStyle(document.body).backgroundColor)
  const [r, g, b] = (background.match(/\d+/g) || []).map(Number)
  check(r + g + b < 200, `暗色主题的背景应是深色：${background}`)
  await shot(dark.page, 'app-center-dark')
  step('暗色主题')
}

async function deployedMode(browser, problems) {
  const ownerEmail = `ac-deployed-${run}@browser.example.com`
  const [, workspace] = seed('user', ownerEmail, '应用中心部署验收')
  const { page } = await login(browser, ownerEmail, problems)
  await openAs(page, workspace, '/settings/modules?module=news')
  const settings = await api(page, workspace, 'GET', '/api/v1/news/settings')
  const saved = await api(page, workspace, 'PUT', '/api/v1/news/settings', { version: settings.body.version, enabled: true, push_times: ['03:30'] })
  check(saved.status === 200, `打开早报推送失败（一个部署只能有一个工作空间启用）：${JSON.stringify(saved.body)}`)
  try {
    await detail(page).waitFor()
    await fact(page, '部署配置').getByText('已完成').waitFor()

    await detail(page).getByRole('button', { name: '检测连接' }).click()
    await page.getByText('「新闻早报」检测完成：连通正常。').waitFor({ timeout: 30000 })
    await fact(page, '服务连通性').getByText('连通正常').waitFor()
    step('检测连接：TrendRadar MCP 连通正常')

    trendradar('pause', 'trendradar-mcp')
    try {
      await detail(page).getByRole('button', { name: '检测连接' }).click()
      await page.getByText('「新闻早报」检测完成：连接失败。').waitFor({ timeout: 40000 })
      await detail(page).getByText(/超时|连不上/).first().waitFor()
    } finally {
      trendradar('unpause', 'trendradar-mcp')
    }
    step('暂停 trendradar-mcp：检测有界超时，显示连接失败')

    // api 容器去掉了全部 capability：用目录的属主（app）改权限，不用 root
    const mode = compose('exec', '-T', 'api', 'stat', '-c', '%a', CONFIG_DIR)
    compose('exec', '-T', 'api', 'chmod', '0555', CONFIG_DIR)
    try {
      await switchTo(page, false)
      await detail(page).getByText('开关已保存，外部配置同步失败').waitFor()
      await fact(page, '外部配置同步').getByText('外部配置文件没有写成').waitFor()
      await card(page).getByText('同步失败', { exact: true }).waitFor()
      await card(page).getByText('开关已保存，外部配置同步失败：外部配置文件没有写成。').waitFor()
      await shot(page, 'app-center-sync-failed')
    } finally {
      compose('exec', '-T', 'api', 'chmod', mode, CONFIG_DIR)
    }
    check((await management(page, workspace)).module.enabled === false, '同步失败不应回滚开关')
    await detail(page).getByRole('button', { name: '重试同步' }).click()
    await page.getByText('「新闻早报」的外部配置已重新同步。').waitFor()
    await fact(page, '外部配置同步').getByText('已同步').waitFor()
    const config = JSON.parse(compose('exec', '-T', 'api', 'cat', `${CONFIG_DIR}/config.yaml`).split('\n').slice(1).join('\n'))
    check(config.notification.enabled === false, '停用后配置文件应不推送')
    await shot(page, 'app-center-retry')
    step('配置目录不可写时停用：开关已保存、同步失败；恢复后重试同步成功，配置文件不推送')

    await switchTo(page, true)
    await fact(page, '外部配置同步').getByText('已同步').waitFor()
  } finally {
    // 无论成败都关掉自己的早报推送（一个部署只能有一个工作空间启用，后面的验收还要用）；模块停用时设置接口 404，先启用
    await api(page, workspace, 'PATCH', '/api/v1/modules/news', { enabled: true })
    const latest = await api(page, workspace, 'GET', '/api/v1/news/settings')
    const released = await api(page, workspace, 'PUT', '/api/v1/news/settings', { version: latest.body.version, enabled: false, push_times: ['03:30'] })
    check(released.status === 200, `关掉早报推送失败：${released.status}`)
  }
  step('再启用同步成功；关掉自己的早报推送')
}

;(async () => {
  const browser = await chromium.launch()
  const problems = []
  try {
    if (deployed) await deployedMode(browser, problems)
    else await composeMode(browser, problems)
    check(problems.length === 0, `页面有错误：\n${problems.join('\n')}`)
    console.log(`应用中心浏览器验收通过${deployed ? '（已部署 TrendRadar）' : ''}`)
  } finally {
    await browser.close()
  }
})().catch((error) => { console.error(error); process.exit(1) })
