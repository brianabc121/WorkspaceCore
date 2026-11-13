// 大模型会话桥浏览器验收（T-0229，docs/v2/LLM_BRIDGE_PLAN.md、docs/deployment.md 第 25 节）：
// 真实后端 + 真实前端 + 真实的会话桥（里面是锁定版本的 codex / claude / codebuddy 官方程序，都没有登录）——
// 应用中心启用模块 → 登记会话桥（令牌不回显）→ 三个账号槽位的状态与通道 → 用 API 密钥登录 / 退出 WorkBuddy →
// Claude 订阅登录先确认风险 → 改设置 → 开发环境增删 → 会话页与同步 → 给实例授权 → 令牌不对时原地提示 →
// admin 只读、member 看不到 → 手机宽度 → 停用与删除登记。
// 这个脚本同时验证“工作台能连到会话桥”：CI 里会话桥用 make llm-bridge-up 启动，加入工作台的内部网络，
// 登记的地址是 http://llm-bridge:8787。
//
// 不验证的：各平台的真实登录和真实回合（需要账号）；智能体经 /mcp 调用工具（compose job 没有接入入口，
// 由 services/api/tests/test_module_llm_bridge.py 覆盖）。
//
// 前提：Compose 全栈已启动并迁移（make up && make migrate），会话桥已启动（make llm-bridge-up）。
// 用法（依赖由同目录的 package.json / package-lock.json 锁定）：
//   LLM_BRIDGE_ADMIN_TOKEN=<会话桥的管理令牌> node llm_bridge_browser.cjs [BASE_URL]
//   可选：LLM_BRIDGE_URL（工作台访问会话桥的地址，默认 http://llm-bridge:8787）
// 不要求空库：每次新建工作台用户。结束时（成败都）删掉登记、关掉模块。
const { execFileSync } = require('node:child_process')
const { randomBytes } = require('node:crypto')
const path = require('node:path')
const { chromium } = require('playwright')

const base = (process.argv[2] || `http://127.0.0.1:${process.env.HTTP_PORT || '8080'}`).replace(/\/$/, '')
const bridgeUrl = process.env.LLM_BRIDGE_URL || 'http://llm-bridge:8787'
const bridgeToken = process.env.LLM_BRIDGE_ADMIN_TOKEN || ''
const password = 'e2e llm bridge browser pass'
const run = randomBytes(4).toString('hex')
const ownerEmail = `lb-owner-${run}@browser.example.com`
const adminEmail = `lb-admin-${run}@browser.example.com`
const memberEmail = `lb-member-${run}@browser.example.com`
const BRIDGE = `验收桥 ${run}`
const INSTANCE = `lb-agent-${run}`
// 假的 API 密钥：只用来验证“保存、显示为已登录、不回显、能退出”，不会发给任何平台
const FAKE_KEY = `e2e-not-a-real-key-${randomBytes(12).toString('hex')}`
const WORKBUDDY = 'WorkBuddy（CodeBuddy）'

function seed(...args) {
  return execFileSync('python3', [path.join(__dirname, 'seed.py'), ...args], {
    encoding: 'utf8',
    env: { ...process.env, E2E_PASSWORD: password },
  }).trim().split(/\s+/)
}
function check(condition, message) { if (!condition) throw new Error(message) }
const step = (text) => console.log(`通过  ${text}`)

async function api(page, workspace, method, pathname, body) {
  return page.evaluate(async ({ method, pathname, body, workspace }) => {
    const csrf = document.cookie.split('; ').find((item) => item.startsWith('ws_csrf='))?.slice(8)
    const headers = { 'X-Workspace-Id': workspace, ...(csrf ? { 'X-CSRF-Token': decodeURIComponent(csrf) } : {}) }
    if (body !== undefined) headers['Content-Type'] = 'application/json'
    const response = await fetch(pathname, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) })
    const text = await response.text()
    let json = null
    try { json = text ? JSON.parse(text) : null } catch { /* 不是 JSON */ }
    return { status: response.status, body: json }
  }, { method, pathname, body, workspace })
}

function watch(page, problems, who) {
  page.on('pageerror', (error) => problems.push(`${who} 页面错误：${error.message}`))
  page.on('response', (response) => {
    // 503 是“会话桥连不上 / 令牌不对”的预期响应，页面要在原地显示说明；其他 5xx 都算问题
    if (response.status() >= 500 && response.status() !== 503) problems.push(`${who} ${response.status()} ${response.request().method()} ${new URL(response.url()).pathname}`)
  })
  page.on('console', (message) => {
    // 4xx / 503 在浏览器里会记成“资源加载失败”；这些响应由下面的断言逐个检查
    if (message.type() === 'error' && !/Failed to load resource/i.test(message.text())) problems.push(`${who} 控制台错误：${message.text()}`)
  })
}
async function login(browser, email, problems, who, viewport = { width: 1440, height: 1000 }) {
  const context = await browser.newContext({ viewport, locale: 'zh-CN' })
  const page = await context.newPage()
  watch(page, problems, who)
  await page.goto(`${base}/login`)
  await page.getByLabel('邮箱').fill(email)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '登录' }).click()
  await page.waitForURL(`${base}/`)
  return { context, page }
}
const sidebar = (page) => page.getByRole('navigation', { name: '主导航' })
// seed.py 给每个用户都建了自己的空间：别的用户打开页面后要切到 owner 的工作空间
async function openAs(page, workspace, target) {
  await page.goto(`${base}${target}`)
  const select = page.getByLabel('当前工作空间')
  await select.waitFor()
  if ((await select.inputValue()) !== workspace) await select.selectOption(workspace)
}
async function noHorizontalScroll(page) {
  return !(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth))
}
// 页面用 window.confirm 确认危险操作：接受下一次弹出的确认框
const acceptNext = (page) => page.once('dialog', (dialog) => dialog.accept())
const account = (page, label) => page.getByRole('article', { name: `账号：${label}` })
const tab = (page, name) => page.getByRole('tab', { name, exact: true })

async function main() {
  check(bridgeToken.length >= 32, '请设置 LLM_BRIDGE_ADMIN_TOKEN（会话桥的管理令牌）')
  const [ownerId, workspace] = seed('user', ownerEmail, '会话桥验收')
  const [adminId] = seed('user', adminEmail, '会话桥管理员')
  const [memberId] = seed('user', memberEmail, '会话桥成员')
  seed('member', workspace, adminId, 'admin')
  seed('member', workspace, memberId, 'member')
  check(ownerId && workspace, '没有建出测试用户')

  const browser = await chromium.launch()
  const problems = []
  let ownerPage = null
  try {
    const { page } = await login(browser, ownerEmail, problems, 'owner')
    ownerPage = page

    // ---------------------------------------------------------------- 默认停用 → 应用中心启用
    const before = await api(page, workspace, 'GET', '/api/v1/llm_bridge/bridges')
    check(before.status === 404 && before.body.error.code === 'MODULE_DISABLED', `模块默认应停用：${JSON.stringify(before)}`)
    check(await sidebar(page).getByRole('link', { name: '大模型会话桥', exact: true }).count() === 0, '停用时侧栏不应有入口')
    await page.goto(`${base}/settings/modules`)
    await page.getByRole('heading', { name: '应用中心' }).waitFor()
    const toggle = page.getByRole('article', { name: '模块：大模型会话桥' }).getByRole('switch', { name: '启用大模型会话桥' })
    check((await toggle.getAttribute('aria-checked')) === 'false', '大模型会话桥默认应停用')
    await toggle.click()
    await page.waitForFunction(() => document.querySelector('[role="switch"][aria-label="启用大模型会话桥"]')?.getAttribute('aria-checked') === 'true')
    step('模块默认停用（接口 404 MODULE_DISABLED、侧栏没有入口），在应用中心启用')

    // ---------------------------------------------------------------- 登记会话桥
    await page.goto(`${base}/llm_bridge`)
    await page.getByRole('heading', { name: '大模型会话桥', level: 1 }).waitFor()
    await sidebar(page).getByRole('link', { name: '大模型会话桥', exact: true }).waitFor()
    await page.getByText('还没有登记会话桥。').waitFor()
    await page.getByRole('button', { name: '添加会话桥' }).click()
    let form = page.getByRole('form', { name: '添加会话桥' })
    await form.getByLabel('名字').fill(BRIDGE)
    await form.getByLabel('地址').fill(bridgeUrl)
    await form.getByLabel('管理令牌').fill('too-short')
    await form.getByRole('button', { name: '登记' }).click()
    await page.getByText('令牌至少 32 个字符').first().waitFor()
    await form.getByLabel('管理令牌').fill(bridgeToken)
    await form.getByRole('button', { name: '登记' }).click()
    await page.getByText(`已登记「${BRIDGE}」。`).waitFor()
    const row = page.getByRole('row').filter({ hasText: BRIDGE })
    await row.getByText('已登记令牌').waitFor()
    await row.getByText('已启用').waitFor()
    check(!(await page.content()).includes(bridgeToken), '管理令牌不应留在页面上')
    const listed = await api(page, workspace, 'GET', '/api/v1/llm_bridge/bridges')
    check(listed.status === 200 && listed.body.bridges.length === 1 && listed.body.bridges[0].has_token, `登记后的列表不对：${JSON.stringify(listed)}`)
    check(!JSON.stringify(listed.body).includes(bridgeToken), '接口不应回传管理令牌')
    const bridgeId = listed.body.bridges[0].id
    step('登记会话桥：令牌太短被拦下；登记后显示“已登记令牌”，页面和接口都不回显令牌')

    // ---------------------------------------------------------------- 状态：三个账号槽位与通道（真实的官方程序，都没登录）
    await account(page, 'ChatGPT / Codex').getByText('未登录').waitFor({ timeout: 60000 })
    await account(page, WORKBUDDY).getByText('未登录').waitFor()
    // Claude：容器里有官方程序时是“未登录”；本机验收没有接 Claude 程序时是“不可用”
    const claudeState = await account(page, 'Claude').locator('.asv-state').first().innerText()
    check(['未登录', '不可用'].includes(claudeState.trim()), `Claude 槽位的状态不对：${claudeState}`)
    const channels = page.getByRole('table', { name: '通道' })
    await channels.getByRole('row').filter({ hasText: 'ChatGPT' }).getByText('可用', { exact: true }).waitFor()
    const cloud = channels.getByRole('row').filter({ hasText: 'Codex' }).filter({ hasText: '云端' })
    await cloud.getByText('不可用').waitFor()
    await cloud.getByText('接口桩').waitFor()
    await page.getByRole('group', { name: '回合' }).getByText('排队中 0').waitFor()
    step(`状态页：ChatGPT / Codex、WorkBuddy 未登录，Claude ${claudeState.trim()}；通道表里云端通道标为不可用（接口桩）`)

    // ---------------------------------------------------------------- 用 API 密钥登录 / 退出 WorkBuddy
    await account(page, WORKBUDDY).getByRole('button', { name: `登录${WORKBUDDY}` }).click()
    form = page.getByRole('form', { name: `登录${WORKBUDDY}` })
    await form.getByRole('button', { name: '登录', exact: true }).click()
    await page.getByText('请填写API 密钥。').waitFor()
    await form.getByRole('textbox', { name: 'API 密钥' }).fill(FAKE_KEY)
    await form.getByRole('button', { name: '登录', exact: true }).click()
    await page.getByText(`「${WORKBUDDY}」已登录。`).waitFor()
    await account(page, WORKBUDDY).getByText('已登录').waitFor()
    await account(page, WORKBUDDY).locator('dd', { hasText: 'API 密钥' }).waitFor()
    // 用密钥登录时会话桥看不到是哪个账号：页面如实说明，不拿密钥冒充账号
    await account(page, WORKBUDDY).getByText('换一把密钥不算换账号').waitFor()
    check(!(await page.content()).includes(FAKE_KEY), 'API 密钥不应留在页面上')
    const afterLogin = await api(page, workspace, 'GET', `/api/v1/llm_bridge/bridges/${bridgeId}/status`)
    check(!JSON.stringify(afterLogin.body).includes(FAKE_KEY), '状态接口不应回传 API 密钥')
    acceptNext(page)
    await account(page, WORKBUDDY).getByRole('button', { name: `退出登录${WORKBUDDY}` }).click()
    await page.getByText(`已退出「${WORKBUDDY}」的登录。`).waitFor()
    await account(page, WORKBUDDY).getByText('未登录').waitFor()
    // 退出后登记还在：提示“要换成别的账号先点更换账号”，但不再显示登录方式
    await account(page, WORKBUDDY).getByText('这座会话桥登记过这个平台的账号').waitFor()
    await account(page, WORKBUDDY).getByRole('button', { name: `更换账号${WORKBUDDY}` }).waitFor()
    check(await account(page, WORKBUDDY).locator('dd', { hasText: 'API 密钥' }).count() === 0, '退出登录后不应再显示登录方式')
    step('WorkBuddy：不填密钥被拦下；用 API 密钥登录后显示已登录、密钥不回显；退出后回到未登录，登记保留')

    // ---------------------------------------------------------------- Claude 订阅登录：先确认风险
    await account(page, 'Claude').getByRole('button', { name: '登录Claude' }).click()
    form = page.getByRole('form', { name: '登录Claude' })
    await form.getByRole('radio', { name: '订阅令牌' }).check()
    const risk = form.getByRole('group', { name: 'Claude 订阅登录的风险确认' })
    await risk.getByText('Anthropic 要求开发者用 API 密钥接入').waitFor()
    check(await form.getByRole('button', { name: '登录', exact: true }).isDisabled(), '确认风险之前不能提交订阅登录')
    const blocked = await api(page, workspace, 'POST', `/api/v1/llm_bridge/bridges/${bridgeId}/accounts/claude/login`, { method: 'oauth_token', oauth_token: 'x' })
    check(blocked.status === 403, `没确认风险时后端也要拒绝订阅登录：${JSON.stringify(blocked)}`)
    await risk.getByRole('checkbox').check()
    await risk.waitFor({ state: 'detached' })
    check(!(await form.getByRole('button', { name: '登录', exact: true }).isDisabled()), '确认风险之后应能提交')
    await form.getByRole('button', { name: '取消' }).click()
    step('Claude：订阅登录要先勾选风险确认（没确认时页面不能提交、后端 403）；确认后可以继续')

    // ---------------------------------------------------------------- 设置
    await tab(page, '设置').click()
    form = page.getByRole('form', { name: '会话桥设置' })
    await form.getByText('已确认').waitFor()
    await form.getByLabel('同时进行的回合数').fill('99')
    await form.getByRole('button', { name: '保存设置' }).click()
    await page.getByText('「同时进行的回合数」要填 1–16 之间的整数。').waitFor()
    await form.getByLabel('同时进行的回合数').fill('3')
    await form.getByRole('button', { name: '保存设置' }).click()
    await page.getByText('设置已保存。').waitFor()
    await page.reload()
    await tab(page, '设置').click()
    await page.waitForFunction(() => document.querySelector('#lb-set-concurrency')?.value === '3')
    step('设置：超出范围被拦下；并发改成 3，刷新后还在')

    // ---------------------------------------------------------------- 开发环境
    await tab(page, '开发环境').click()
    await page.getByText('还没有开发环境。').waitFor()
    await page.getByRole('button', { name: '新增环境' }).click()
    form = page.getByRole('form', { name: '新增开发环境' })
    await form.getByLabel('平台').selectOption({ label: 'Claude' })
    await form.getByLabel('权限').selectOption({ label: '可执行命令' })
    await form.getByRole('note').filter({ hasText: '没有操作系统级沙箱' }).waitFor()
    await form.getByLabel('平台').selectOption({ label: 'Codex' })
    await form.getByLabel('权限').selectOption({ label: '可写工作区' })
    await form.getByLabel('名字').fill('验收环境')
    await form.getByLabel('代码仓库地址（可不填）').fill('http://example.com/a.git')
    await form.getByRole('button', { name: '保存' }).click()
    await page.getByText('代码仓库地址只支持 https:// 开头的地址。').waitFor()
    await form.getByLabel('代码仓库地址（可不填）').fill('')
    await form.getByRole('button', { name: '保存' }).click()
    await page.getByText('已新增环境「验收环境」。').waitFor()
    const envRow = page.getByRole('table', { name: '开发环境' }).getByRole('row').filter({ hasText: '验收环境' })
    await envRow.getByText('可写工作区').waitFor()
    await envRow.getByText('不联网').waitFor()
    acceptNext(page)
    await envRow.getByRole('button', { name: '删除环境验收环境' }).click()
    await page.getByText('已删除环境「验收环境」。').waitFor()
    await page.getByText('还没有开发环境。').waitFor()
    step('开发环境：Claude 选“可执行命令”有风险提示；非 https 的仓库地址被拦下；新增后列出，删除后消失')

    // ---------------------------------------------------------------- 会话
    await tab(page, '会话').click()
    await page.getByText('还没有会话。').waitFor()
    await page.getByRole('button', { name: '同步官方程序里的会话' }).click()
    await page.getByText('已同步官方程序里的会话。').waitFor({ timeout: 60000 })
    step('会话：空列表有说明；同步官方程序里的会话成功')

    // ---------------------------------------------------------------- 实例权限
    await page.getByText('这个工作空间还没有智能体实例。').waitFor()
    const instance = await api(page, workspace, 'POST', '/api/v1/agents/instances', { name: INSTANCE, network_mode: 'intranet', scope: 'read_write', max_role: 'member' })
    check(instance.status === 201, `没有建出实例：${JSON.stringify(instance)}`)
    await page.reload()
    const grant = page.getByRole('group', { name: `实例权限：${INSTANCE}`, exact: true })
    await grant.getByText('未启用').waitFor()
    check(await grant.getByLabel('新建会话和发送').isDisabled(), '没启用时不能单独勾选权限')
    await grant.getByLabel('启用', { exact: true }).check()
    await page.getByText(`已保存「${INSTANCE}」的权限。`).waitFor()
    await grant.getByLabel('新建会话和发送').check()
    await grant.getByText('已启用').waitFor()
    await grant.getByRole('group', { name: `${INSTANCE}允许的平台` }).getByLabel('Codex').check()
    await page.waitForFunction(async ({ workspace, id }) => {
      const response = await fetch('/api/v1/llm_bridge/grants', { headers: { 'X-Workspace-Id': workspace } })
      const found = (await response.json()).grants.find((g) => g.instance_id === id)
      return found && found.enabled && found.can_write && !found.can_account && found.platforms.join() === 'codex'
    }, { workspace, id: instance.body.id })
    // 关掉“启用”：权限一并收回，已选的平台保留（再打开时不用重选）
    await grant.getByLabel('启用', { exact: true }).uncheck()
    await grant.getByText('未启用').waitFor()
    const off = await api(page, workspace, 'GET', '/api/v1/llm_bridge/grants')
    const saved = off.body.grants.find((g) => g.instance_id === instance.body.id)
    check(saved && !saved.enabled && !saved.can_write && saved.platforms.join() === 'codex', `关闭后的权限不对：${JSON.stringify(saved)}`)
    step('实例权限：默认未启用；启用并勾选“新建会话和发送”、只允许 Codex；关掉后权限收回、平台选择保留')

    // ---------------------------------------------------------------- 令牌不对：原地提示，不当成页面出错
    const bridgeRow = () => page.getByRole('row').filter({ hasText: BRIDGE })
    await bridgeRow().getByRole('button', { name: `修改${BRIDGE}` }).click()
    form = page.getByRole('form', { name: '修改会话桥' })
    await form.getByLabel('管理令牌').fill('x'.repeat(40))
    await form.getByRole('button', { name: '保存' }).click()
    await page.getByText(`已保存「${BRIDGE}」。`).waitFor()
    await tab(page, '状态与登录').click()
    await page.getByRole('alert').filter({ hasText: '连不上这座会话桥' }).filter({ hasText: '令牌' }).waitFor()
    await bridgeRow().getByRole('button', { name: `修改${BRIDGE}` }).click()
    form = page.getByRole('form', { name: '修改会话桥' })
    await form.getByLabel('管理令牌').fill(bridgeToken)
    await form.getByRole('button', { name: '保存' }).click()
    await page.getByText(`已保存「${BRIDGE}」。`).waitFor()
    await account(page, 'ChatGPT / Codex').getByText('未登录').waitFor({ timeout: 60000 })
    step('令牌改错后状态页原地提示“连不上这座会话桥…令牌”；改回后恢复')

    // ---------------------------------------------------------------- admin 只读、member 看不到
    const admin = await login(browser, adminEmail, problems, 'admin')
    await openAs(admin.page, workspace, '/llm_bridge')
    await admin.page.getByText('只有 owner 能修改大模型会话桥；你看到的是只读视图。').waitFor()
    await admin.page.getByRole('row').filter({ hasText: BRIDGE }).waitFor()
    await account(admin.page, 'ChatGPT / Codex').getByText('未登录').waitFor({ timeout: 60000 })
    check(await admin.page.getByRole('button', { name: '添加会话桥' }).count() === 0, 'admin 不应看到“添加会话桥”')
    check(await admin.page.getByRole('button', { name: '登录ChatGPT / Codex' }).count() === 0, 'admin 不应看到登录按钮')
    const adminWrite = await api(admin.page, workspace, 'POST', '/api/v1/llm_bridge/bridges', { name: 'x', base_url: bridgeUrl, token: bridgeToken })
    check(adminWrite.status === 403, `admin 不能登记会话桥：${adminWrite.status}`)
    const adminGrant = await api(admin.page, workspace, 'PUT', `/api/v1/llm_bridge/grants/${instance.body.id}`, { enabled: true })
    check(adminGrant.status === 403, `admin 不能改实例权限：${adminGrant.status}`)

    const member = await login(browser, memberEmail, problems, 'member')
    await openAs(member.page, workspace, '/')
    await sidebar(member.page).getByRole('link', { name: '任务', exact: true }).waitFor()
    check(await sidebar(member.page).getByRole('link', { name: '大模型会话桥', exact: true }).count() === 0, 'member 侧栏不应有大模型会话桥')
    const memberRead = await api(member.page, workspace, 'GET', '/api/v1/llm_bridge/bridges')
    check(memberRead.status === 403, `member 不能查看会话桥：${memberRead.status}`)
    step('admin 看到只读视图、接口写操作 403；member 侧栏没有入口、接口 403')

    // ---------------------------------------------------------------- 手机宽度
    const phone = await login(browser, ownerEmail, problems, 'owner（手机）', { width: 390, height: 844 })
    await phone.page.goto(`${base}/llm_bridge`)
    await account(phone.page, 'ChatGPT / Codex').getByText('未登录').waitFor({ timeout: 60000 })
    check(await noHorizontalScroll(phone.page), '手机宽度下页面不应横向滚动')
    for (const name of ['设置', '开发环境', '会话']) {
      await tab(phone.page, name).click()
      await phone.page.getByRole('tabpanel', { name }).waitFor()
      check(await noHorizontalScroll(phone.page), `手机宽度下“${name}”不应横向滚动`)
    }
    step('手机宽度（390）：四个标签都不横向滚动')

    // ---------------------------------------------------------------- 停用与删除登记
    acceptNext(page)
    await bridgeRow().getByRole('button', { name: `停用${BRIDGE}` }).click()
    await page.getByText(`已停用「${BRIDGE}」。`).waitFor()
    await page.getByRole('note').filter({ hasText: '这座会话桥已停用' }).waitFor()
    acceptNext(page)
    await bridgeRow().getByRole('button', { name: `删除${BRIDGE}` }).click()
    await page.getByText(`已删除「${BRIDGE}」的登记。`).waitFor()
    await page.getByText('还没有登记会话桥。').waitFor()
    step('停用后状态页有提示；删除登记后回到空状态')

    check(problems.length === 0, `检查过程中出现错误：\n  ${problems.join('\n  ')}`)
    console.log('大模型会话桥浏览器验收全部通过')
  } finally {
    // 成败都收尾：删掉这次登记的会话桥、关掉模块（别的验收脚本按“模块默认停用”检查）
    if (ownerPage) {
      try {
        const left = await api(ownerPage, workspace, 'GET', '/api/v1/llm_bridge/bridges')
        for (const bridge of left.body?.bridges ?? []) await api(ownerPage, workspace, 'DELETE', `/api/v1/llm_bridge/bridges/${bridge.id}`)
        await api(ownerPage, workspace, 'PATCH', '/api/v1/modules/llm_bridge', { enabled: false })
      } catch (error) { console.error(`收尾失败：${error.message}`) }
    }
    await browser.close()
  }
}

main().catch((error) => { console.error(`失败  ${error.message}`); process.exit(1) })
