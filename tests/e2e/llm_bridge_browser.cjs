// 大模型客户端管理浏览器验收（T-0229 / T-0230，docs/v2/LLM_BRIDGE_PLAN.md 第 19 节、docs/deployment.md 第 25 节）：
// 真实后端 + 真实前端 + 真实的 MCP 管理器（会话桥容器，里面是锁定版本的 codex / claude / codebuddy 官方程序，都没有登录）——
// 应用中心启用模块 → 添加 1 号管理器（只控制 Claude / WorkBuddy；令牌不对不添加、令牌不回显）→
// 添加 2 号管理器（控制 Codex / Claude / WorkBuddy）→ 两个管理器各自一套账号（在 1 号登录不影响 2 号）→
// 改控制的平台（取消要确认、不能一个都不留）→ Claude 订阅登录先确认风险 → 设置 → 开发环境（只能选控制的平台）→
// 会话 → 实例权限（含只允许用某个管理器）→ 令牌改错时原地提示 → admin 只读、member 看不到 → 手机宽度 → 停用与删除。
// 这个脚本同时验证“工作台能连到每个 MCP 管理器”：CI 里用 make llm-bridge-up 和 make llm-bridge-up N=2 启动，
// 它们加入工作台的内部网络，地址是 http://llm-bridge:8787 和 http://llm-bridge-2:8787。
//
// 不验证的：各平台的真实登录和真实回合（需要账号）；智能体经 /mcp 调用工具（compose job 没有接入入口，
// 由 services/api/tests/test_module_llm_bridge.py 覆盖）。
//
// 前提：Compose 全栈已启动并迁移（make up && make migrate），MCP 管理器已启动（make llm-bridge-up，可选 N=2）。
// 用法（依赖由同目录的 package.json / package-lock.json 锁定）：
//   LLM_BRIDGE_ADMIN_TOKEN=<1 号的管理令牌> [LLM_BRIDGE_ADMIN_TOKEN_2=<2 号的管理令牌>] node llm_bridge_browser.cjs [BASE_URL]
//   可选：LLM_BRIDGE_URL、LLM_BRIDGE_URL_2（工作台访问它们的地址，默认 http://llm-bridge:8787、http://llm-bridge-2:8787）
//   没有给 2 号的令牌时跳过需要两个管理器的检查（会打印“跳过”）。
// 不要求空库：每次新建工作台用户。结束时（成败都）把控制的平台改回全部、删掉添加的管理器、关掉模块。
const { execFileSync } = require('node:child_process')
const { randomBytes } = require('node:crypto')
const path = require('node:path')
const { chromium } = require('playwright')

const base = (process.argv[2] || `http://127.0.0.1:${process.env.HTTP_PORT || '8080'}`).replace(/\/$/, '')
const bridgeUrl = process.env.LLM_BRIDGE_URL || 'http://llm-bridge:8787'
const bridgeToken = process.env.LLM_BRIDGE_ADMIN_TOKEN || ''
const secondUrl = process.env.LLM_BRIDGE_URL_2 || 'http://llm-bridge-2:8787'
const secondToken = process.env.LLM_BRIDGE_ADMIN_TOKEN_2 || ''
const password = 'e2e llm bridge browser pass'
const run = randomBytes(4).toString('hex')
const ownerEmail = `lb-owner-${run}@browser.example.com`
const adminEmail = `lb-admin-${run}@browser.example.com`
const memberEmail = `lb-member-${run}@browser.example.com`
const ONE = `1 号 ${run}`
const TWO = `2 号 ${run}`
const ALL_PLATFORMS = ['chatgpt', 'codex', 'claude', 'workbuddy']
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
    // 503 是“MCP 管理器连不上 / 令牌不对”的预期响应，页面要在原地显示说明；其他 5xx 都算问题
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
const card = (page, name) => page.getByRole('article', { name: `MCP 管理器：${name}` })
const panel = (page) => page.getByRole('tabpanel', { name: '平台与账号' })
const control = (page) => panel(page).getByRole('group', { name: '控制的平台' })
// 卡片上“控制的平台”那几行的文字，例如 ['Claude未登录', 'WorkBuddy（CodeBuddy）…']
async function platformsOn(page, name) {
  const list = card(page, name).getByRole('list', { name: `${name}控制的平台` })
  await list.waitFor({ timeout: 60000 })
  return (await list.getByRole('listitem').allInnerTexts()).map((text) => text.replace(/\s+/g, ''))
}
async function addManager(page, { name, url, token, uncheck }) {
  await page.getByRole('button', { name: '添加 MCP 管理器', exact: true }).click()
  // 这个工作台带着管理器的构建文件（T-0234）：默认打开的是“一键部署到服务器”，这里验收手动添加
  const manual = page.getByRole('group', { name: '添加方式' }).getByRole('button', { name: /手动添加/ })
  await manual.waitFor({ timeout: 30000 })
  check(await page.getByRole('form', { name: '一键部署 MCP 管理器' }).count() === 1, '添加管理器时应先给出“一键部署到服务器”')
  await manual.click()
  const form = page.getByRole('form', { name: '添加 MCP 管理器' })
  await form.getByLabel('名字').fill(name)
  await form.getByLabel('地址').fill(url)
  await form.getByLabel('管理令牌').fill(token)
  for (const platform of uncheck) await form.getByRole('group', { name: '控制的平台' }).getByLabel(platform, { exact: true }).uncheck()
  await form.getByRole('button', { name: '添加', exact: true }).click()
  return form
}

async function main() {
  check(bridgeToken.length >= 32, '请设置 LLM_BRIDGE_ADMIN_TOKEN（1 号 MCP 管理器的管理令牌）')
  const two = secondToken.length >= 32
  const [ownerId, workspace] = seed('user', ownerEmail, '大模型客户端验收')
  const [adminId] = seed('user', adminEmail, '大模型客户端管理员')
  const [memberId] = seed('user', memberEmail, '大模型客户端成员')
  seed('member', workspace, adminId, 'admin')
  seed('member', workspace, memberId, 'member')
  check(ownerId && workspace, '没有建出测试用户')

  const browser = await chromium.launch()
  const problems = []
  let ownerPage = null
  try {
    const { page } = await login(browser, ownerEmail, problems, 'owner')
    ownerPage = page
    const bridges = async () => (await api(page, workspace, 'GET', '/api/v1/llm_bridge/bridges')).body.bridges
    const statusOf = async (id) => (await api(page, workspace, 'GET', `/api/v1/llm_bridge/bridges/${id}/status`)).body

    // ---------------------------------------------------------------- 默认停用 → 应用中心启用
    const before = await api(page, workspace, 'GET', '/api/v1/llm_bridge/bridges')
    check(before.status === 404 && before.body.error.code === 'MODULE_DISABLED', `模块默认应停用：${JSON.stringify(before)}`)
    // 模块状态读到之前，前端先按“启用”显示侧栏入口（免得闪一下）：所以重新载入、等状态读到之后，再确认入口不在
    const modulesLoaded = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/v1/modules' && response.request().method() === 'GET' && response.ok())
    await page.reload()
    await modulesLoaded
    await sidebar(page).getByRole('link', { name: '任务', exact: true }).waitFor()
    await sidebar(page).getByRole('link', { name: '大模型客户端', exact: true }).waitFor({ state: 'detached', timeout: 15000 }).catch(() => { throw new Error('停用时侧栏不应有入口') })
    await page.goto(`${base}/settings/modules`)
    await page.getByRole('heading', { name: '应用中心' }).waitFor()
    const toggle = page.getByRole('article', { name: '模块：大模型客户端' }).getByRole('switch', { name: '启用大模型客户端' })
    check((await toggle.getAttribute('aria-checked')) === 'false', '大模型客户端默认应停用')
    await toggle.click()
    await page.waitForFunction(() => document.querySelector('[role="switch"][aria-label="启用大模型客户端"]')?.getAttribute('aria-checked') === 'true')
    step('模块默认停用（接口 404 MODULE_DISABLED、侧栏没有入口），在应用中心启用')

    // ---------------------------------------------------------------- 添加 1 号：只控制 Claude / WorkBuddy
    await page.goto(`${base}/llm_bridge?view=manage`)
    await page.getByRole('heading', { name: '大模型客户端管理', level: 1 }).waitFor()
    await sidebar(page).getByRole('link', { name: '大模型客户端', exact: true }).waitFor()
    await page.getByText('还没有 MCP 管理器。').waitFor()
    let form = await addManager(page, { name: ONE, url: bridgeUrl, token: 'too-short', uncheck: ['ChatGPT', 'Codex'] })
    await page.getByText('令牌至少 32 个字符').first().waitFor()
    // 令牌不对：添加时就验证，不会留下一个连不上的管理器
    await form.getByLabel('管理令牌').fill('x'.repeat(40))
    await form.getByRole('button', { name: '添加', exact: true }).click()
    await page.getByText('MCP 管理器不接受这枚令牌').waitFor()
    check((await bridges()).length === 0, '令牌不对时不应添加')
    await form.getByLabel('管理令牌').fill(bridgeToken)
    await form.getByRole('button', { name: '添加', exact: true }).click()
    await page.getByText(`已添加「${ONE}」。`).waitFor()
    await card(page, ONE).getByText('已启用').waitFor()
    check(!(await page.content()).includes(bridgeToken), '管理令牌不应留在页面上')
    const listed = await bridges()
    check(listed.length === 1 && listed[0].has_token, `添加后的列表不对：${JSON.stringify(listed)}`)
    check(!JSON.stringify(listed).includes(bridgeToken), '接口不应回传管理令牌')
    const oneId = listed[0].id
    const onOne = await platformsOn(page, ONE)
    check(onOne.length === 2 && onOne[0].startsWith('Claude') && onOne[1].startsWith('WorkBuddy'), `1 号卡片上控制的平台不对：${onOne}`)
    step('添加 1 号管理器（只勾 Claude / WorkBuddy）：令牌太短、令牌不对都被拦下且不添加；添加后令牌不回显，卡片列出它控制的两个平台')

    // ---------------------------------------------------------------- 1 号的平台与账号：只有它控制的平台
    await account(page, WORKBUDDY).getByText('未登录').waitFor({ timeout: 60000 })
    // Claude：容器里有官方程序时是“未登录”；本机验收没有接 Claude 程序时是“不可用”
    const claudeState = await account(page, 'Claude').locator('.asv-state').first().innerText()
    check(['未登录', '不可用'].includes(claudeState.trim()), `Claude 的状态不对：${claudeState}`)
    check(await account(page, 'ChatGPT / Codex').count() === 0, '1 号不控制 ChatGPT / Codex，不应显示它的账号')
    for (const [name, on] of [['ChatGPT', false], ['Codex', false], ['Claude', true], ['WorkBuddy', true]]) {
      check((await control(page).getByLabel(name, { exact: true }).isChecked()) === on, `1 号“控制的平台”里 ${name} 的勾选不对`)
    }
    const channels = page.getByRole('table', { name: '通道' })
    await channels.getByRole('row').filter({ hasText: 'Claude' }).filter({ hasText: '对话' }).getByText('可用', { exact: true }).waitFor()
    const cloud = channels.getByRole('row').filter({ hasText: 'Claude' }).filter({ hasText: '云端' })
    await cloud.getByText('不可用').waitFor()
    await cloud.getByText('接口桩').waitFor()
    check(await channels.getByRole('row').filter({ hasText: 'Codex' }).count() === 0, '通道表不应列出 1 号不控制的平台')
    await page.getByRole('group', { name: '回合' }).getByText('排队中 0').waitFor()
    const refusedLogin = await api(page, workspace, 'POST', `/api/v1/llm_bridge/bridges/${oneId}/accounts/openai/login`, { method: 'api_key', api_key: FAKE_KEY })
    check(refusedLogin.status === 409, `1 号不控制 ChatGPT / Codex，后端也要拒绝在它上面登录：${JSON.stringify(refusedLogin)}`)
    step(`1 号的“平台与账号”：只有 Claude（${claudeState.trim()}）和 WorkBuddy 的账号与通道；不控制的平台后端也拒绝登录`)

    // ---------------------------------------------------------------- 添加 2 号：控制 Codex / Claude / WorkBuddy
    let twoId = null
    if (two) {
      // 每个管理器有自己的令牌：1 号的令牌用不到 2 号上
      form = await addManager(page, { name: TWO, url: secondUrl, token: bridgeToken, uncheck: ['ChatGPT'] })
      await page.getByText('MCP 管理器不接受这枚令牌').waitFor()
      check((await bridges()).length === 1, '用 1 号的令牌不应添加 2 号')
      await form.getByLabel('管理令牌').fill(secondToken)
      await form.getByRole('button', { name: '添加', exact: true }).click()
      await page.getByText(`已添加「${TWO}」。`).waitFor()
      twoId = (await bridges()).find((b) => b.name === TWO).id
      const onTwo = await platformsOn(page, TWO)
      check(onTwo.length === 3 && onTwo[0].startsWith('Codex') && onTwo[1].startsWith('Claude') && onTwo[2].startsWith('WorkBuddy'), `2 号卡片上控制的平台不对：${onTwo}`)
      check((await card(page, TWO).getAttribute('aria-current')) === 'true', '添加后应选中新的管理器')
      await account(page, 'ChatGPT / Codex').getByText('未登录').waitFor({ timeout: 60000 })
      check(!(await control(page).getByLabel('ChatGPT', { exact: true }).isChecked()), '2 号没有勾 ChatGPT')
      await channels.getByRole('row').filter({ hasText: 'Codex' }).filter({ hasText: '开发' }).getByText('可用', { exact: true }).waitFor()
      check(await channels.getByRole('row').filter({ hasText: 'ChatGPT' }).count() === 0, '2 号没有勾 ChatGPT，通道表不应列出它')
      step('添加 2 号管理器（勾 Codex / Claude / WorkBuddy）：1 号的令牌用不上；卡片列出三个平台，有 ChatGPT / Codex 的账号但没有 ChatGPT 聊天通道')
    } else console.log('跳过  添加 2 号管理器（没有设置 LLM_BRIDGE_ADMIN_TOKEN_2）')

    // ---------------------------------------------------------------- 各自一套账号：在 1 号登录 WorkBuddy
    await card(page, ONE).getByRole('button', { name: `查看 MCP 管理器：${ONE}` }).click()
    await account(page, WORKBUDDY).getByRole('button', { name: `登录${WORKBUDDY}` }).click()
    form = page.getByRole('form', { name: `登录${WORKBUDDY}` })
    await form.getByRole('button', { name: '登录', exact: true }).click()
    await page.getByText('请填写API 密钥。').waitFor()
    await form.getByRole('textbox', { name: 'API 密钥' }).fill(FAKE_KEY)
    await form.getByRole('button', { name: '登录', exact: true }).click()
    await page.getByText(`「${WORKBUDDY}」已登录。`).waitFor()
    await account(page, WORKBUDDY).getByText('已登录').waitFor()
    await account(page, WORKBUDDY).locator('dd', { hasText: 'API 密钥' }).waitFor()
    // 用密钥登录时看不到是哪个账号：页面如实说明，不拿密钥冒充账号
    await account(page, WORKBUDDY).getByText('换一把密钥不算换账号').waitFor()
    check(!(await page.content()).includes(FAKE_KEY), 'API 密钥不应留在页面上')
    check(!JSON.stringify(await statusOf(oneId)).includes(FAKE_KEY), '状态接口不应回传 API 密钥')
    await card(page, ONE).getByRole('listitem').filter({ hasText: 'WorkBuddy' }).getByText('已登录').waitFor()
    if (two) {
      // 2 号是另一套账号：在 1 号登录不影响它
      await card(page, TWO).getByRole('listitem').filter({ hasText: 'WorkBuddy' }).getByText('未登录').waitFor()
      const other = (await statusOf(twoId)).accounts.find((a) => a.slot === 'workbuddy')
      check(other.state === 'logged_out' && !other.registered, `2 号的 WorkBuddy 不应受 1 号影响：${JSON.stringify(other)}`)
    }
    acceptNext(page)
    await account(page, WORKBUDDY).getByRole('button', { name: `退出登录${WORKBUDDY}` }).click()
    await page.getByText(`已退出「${WORKBUDDY}」的登录。`).waitFor()
    await account(page, WORKBUDDY).getByText('未登录').waitFor()
    // 退出后登记还在：提示“要换成别的账号先点更换账号”，但不再显示登录方式
    await account(page, WORKBUDDY).getByText('这个管理器登记过这个平台的账号').waitFor()
    await account(page, WORKBUDDY).getByRole('button', { name: `更换账号${WORKBUDDY}` }).waitFor()
    check(await account(page, WORKBUDDY).locator('dd', { hasText: 'API 密钥' }).count() === 0, '退出登录后不应再显示登录方式')
    step(`各自一套账号：在 1 号用 API 密钥登录 WorkBuddy（密钥不回显）${two ? '，2 号的 WorkBuddy 仍是未登录' : ''}；退出后回到未登录，登记保留`)

    // ---------------------------------------------------------------- 改控制的平台
    // 取消勾选要确认；不确认就不改
    page.once('dialog', (dialog) => dialog.dismiss())
    await control(page).getByLabel('Claude', { exact: true }).click()
    await page.waitForTimeout(500)
    check((await statusOf(oneId)).platforms.join() === 'claude,workbuddy', '没确认时不应改控制的平台')
    check(await control(page).getByLabel('Claude', { exact: true }).isChecked(), '没确认时勾选框应回到原样')
    acceptNext(page)
    await control(page).getByLabel('Claude', { exact: true }).click()
    await page.getByText(`「${ONE}」不再控制 Claude。`).waitFor()
    check(await account(page, 'Claude').count() === 0, '取消后不应再显示 Claude 的账号')
    check((await platformsOn(page, ONE)).length === 1, '卡片应只剩一个平台')
    // 最后一个不能取消
    await control(page).getByLabel('WorkBuddy', { exact: true }).click()
    await page.getByText('至少要控制一个平台。').waitFor()
    check((await statusOf(oneId)).platforms.join() === 'workbuddy', '不能把控制的平台改成一个都没有')
    // 重新勾选不用确认
    await control(page).getByLabel('Claude', { exact: true }).click()
    await page.getByText(`已让「${ONE}」控制 Claude。`).waitFor()
    await account(page, 'Claude').waitFor()
    check((await statusOf(oneId)).platforms.join() === 'claude,workbuddy', '重新勾选后应恢复')
    step('改控制的平台：取消要确认（不确认不改）；取消后账号和卡片上的平台跟着消失；最后一个不能取消；重新勾选后恢复')

    // ---------------------------------------------------------------- Claude 订阅登录：先确认风险
    await account(page, 'Claude').getByRole('button', { name: '登录Claude' }).click()
    form = page.getByRole('form', { name: '登录Claude' })
    await form.getByRole('radio', { name: '订阅令牌' }).check()
    const risk = form.getByRole('group', { name: 'Claude 订阅登录的风险确认' })
    await risk.getByText('Anthropic 要求开发者用 API 密钥接入').waitFor()
    check(await form.getByRole('button', { name: '登录', exact: true }).isDisabled(), '确认风险之前不能提交订阅登录')
    const blocked = await api(page, workspace, 'POST', `/api/v1/llm_bridge/bridges/${oneId}/accounts/claude/login`, { method: 'oauth_token', oauth_token: 'x' })
    check(blocked.status === 403, `没确认风险时后端也要拒绝订阅登录：${JSON.stringify(blocked)}`)
    await risk.getByRole('checkbox').check()
    await risk.waitFor({ state: 'detached' })
    check(!(await form.getByRole('button', { name: '登录', exact: true }).isDisabled()), '确认风险之后应能提交')
    await form.getByRole('button', { name: '取消' }).click()
    step('Claude：订阅登录要先勾选风险确认（没确认时页面不能提交、后端 403）；确认后可以继续')

    // ---------------------------------------------------------------- 设置
    await tab(page, '设置').click()
    form = page.getByRole('form', { name: 'MCP 管理器设置' })
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
    check((await statusOf(oneId)).platforms.join() === 'claude,workbuddy', '改别的设置不应动控制的平台')
    step('设置：超出范围被拦下；并发改成 3，刷新后还在，控制的平台不受影响')

    // ---------------------------------------------------------------- 开发环境：只能选这个管理器控制的平台
    await tab(page, '开发环境').click()
    await page.getByText('还没有开发环境。').waitFor()
    await page.getByRole('button', { name: '新增环境' }).click()
    form = page.getByRole('form', { name: '新增开发环境' })
    const offered = await form.getByLabel('平台').locator('option').allInnerTexts()
    check(offered.join() === 'Claude,WorkBuddy', `1 号新增环境时只应能选 Claude / WorkBuddy：${offered}`)
    await form.getByLabel('平台').selectOption({ label: 'Claude' })
    await form.getByLabel('权限').selectOption({ label: '可执行命令' })
    await form.getByRole('note').filter({ hasText: '没有操作系统级沙箱' }).waitFor()
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
    step('开发环境：平台只能选 1 号控制的 Claude / WorkBuddy；“可执行命令”有风险提示；非 https 的仓库地址被拦下；新增后列出，删除后消失')

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
    const grantOf = async () => (await api(page, workspace, 'GET', '/api/v1/llm_bridge/grants')).body.grants.find((g) => g.instance_id === instance.body.id)
    const waitGrant = async (test, what) => {
      for (let i = 0; i < 50; i += 1) { if (test(await grantOf())) return; await page.waitForTimeout(200) }
      throw new Error(`${what}：${JSON.stringify(await grantOf())}`)
    }
    await page.reload()
    const grant = page.getByRole('group', { name: `实例权限：${INSTANCE}`, exact: true })
    await grant.getByText('未启用').waitFor()
    check(await grant.getByLabel('新建会话和发送').isDisabled(), '没启用时不能单独勾选权限')
    await grant.getByLabel('启用', { exact: true }).check()
    await page.getByText(`已保存「${INSTANCE}」的权限。`).waitFor()
    await grant.getByLabel('新建会话和发送').check()
    await grant.getByText('已启用').waitFor()
    await grant.getByRole('group', { name: `${INSTANCE}允许的平台` }).getByLabel('Codex').check()
    await waitGrant((g) => g.enabled && g.can_write && !g.can_account && g.platforms.join() === 'codex' && g.bridge_ids === null, '启用后的权限不对')
    // 允许的 MCP 管理器：默认“全部”（包括以后添加的）；取消后是明确的列表
    const managers = grant.getByRole('group', { name: `${INSTANCE}允许的 MCP 管理器` })
    const everything = managers.getByLabel('全部（包括以后添加的）')
    check(await everything.isChecked(), '允许的 MCP 管理器默认应是“全部”')
    check(await managers.getByLabel(ONE, { exact: true }).isDisabled(), '勾着“全部”时不能单独取消某一个')
    await everything.uncheck()
    await waitGrant((g) => Array.isArray(g.bridge_ids) && g.bridge_ids.length === (two ? 2 : 1), '取消“全部”后应是现在这几个的明确列表')
    await managers.getByLabel(ONE, { exact: true }).uncheck()
    if (two) {
      await waitGrant((g) => g.bridge_ids.join() === twoId, '只允许 2 号')
    } else {
      await grant.getByText('这个实例现在一个 MCP 管理器都不能用').waitFor()
      await waitGrant((g) => g.bridge_ids.length === 0, '一个都不允许')
    }
    // 关掉“启用”：权限一并收回，已选的平台和管理器保留（再打开时不用重选）
    await grant.getByLabel('启用', { exact: true }).uncheck()
    await grant.getByText('未启用').waitFor()
    await waitGrant((g) => !g.enabled && !g.can_write && g.platforms.join() === 'codex' && Array.isArray(g.bridge_ids), '关闭后的权限不对')
    step(`实例权限：默认未启用；启用并勾选“新建会话和发送”、只允许 Codex；允许的 MCP 管理器默认全部，取消后${two ? '只留 2 号' : '一个都不留时有提示'}；关掉后权限收回、选择保留`)

    // ---------------------------------------------------------------- 令牌改错：原地提示，不当成页面出错
    await card(page, ONE).getByRole('button', { name: `修改${ONE}` }).click()
    form = page.getByRole('form', { name: '修改 MCP 管理器' })
    check(await form.getByRole('group', { name: '控制的平台' }).count() === 0, '修改时不在这里改控制的平台')
    await form.getByLabel('管理令牌').fill('x'.repeat(40))
    await form.getByRole('button', { name: '保存' }).click()
    await page.getByText(`已保存「${ONE}」。`).waitFor()
    await card(page, ONE).getByRole('button', { name: `查看 MCP 管理器：${ONE}` }).click()
    await tab(page, '平台与账号').click()
    await panel(page).getByRole('alert').filter({ hasText: '连不上这个 MCP 管理器' }).filter({ hasText: '令牌' }).waitFor()
    await card(page, ONE).getByRole('alert').filter({ hasText: '令牌' }).waitFor()
    await card(page, ONE).getByRole('button', { name: `修改${ONE}` }).click()
    form = page.getByRole('form', { name: '修改 MCP 管理器' })
    await form.getByLabel('管理令牌').fill(bridgeToken)
    await form.getByRole('button', { name: '保存' }).click()
    await page.getByText(`已保存「${ONE}」。`).waitFor()
    await account(page, WORKBUDDY).getByText('未登录').waitFor({ timeout: 60000 })
    step('令牌改错后卡片和“平台与账号”都原地提示连不上（令牌）；改回后恢复')

    // ---------------------------------------------------------------- admin 只读、member 看不到
    const admin = await login(browser, adminEmail, problems, 'admin')
    await openAs(admin.page, workspace, '/llm_bridge?view=manage')
    await admin.page.getByText('只有 owner 能修改大模型客户端；你看到的是只读视图。').waitFor()
    await card(admin.page, ONE).waitFor()
    await account(admin.page, WORKBUDDY).getByText('未登录').waitFor({ timeout: 60000 })
    check(await admin.page.getByRole('button', { name: '添加 MCP 管理器' }).count() === 0, 'admin 不应看到“添加 MCP 管理器”')
    check(await admin.page.getByRole('button', { name: `登录${WORKBUDDY}` }).count() === 0, 'admin 不应看到登录按钮')
    check(await control(admin.page).getByLabel('Claude', { exact: true }).isDisabled(), 'admin 不能改控制的平台')
    const adminWrite = await api(admin.page, workspace, 'POST', '/api/v1/llm_bridge/bridges', { name: 'x', base_url: bridgeUrl, token: bridgeToken })
    check(adminWrite.status === 403, `admin 不能添加 MCP 管理器：${adminWrite.status}`)
    const adminPlatforms = await api(admin.page, workspace, 'PUT', `/api/v1/llm_bridge/bridges/${oneId}/settings`, { platforms: ['claude'] })
    check(adminPlatforms.status === 403, `admin 不能改控制的平台：${adminPlatforms.status}`)
    const adminGrant = await api(admin.page, workspace, 'PUT', `/api/v1/llm_bridge/grants/${instance.body.id}`, { enabled: true })
    check(adminGrant.status === 403, `admin 不能改实例权限：${adminGrant.status}`)

    const member = await login(browser, memberEmail, problems, 'member')
    await openAs(member.page, workspace, '/')
    await sidebar(member.page).getByRole('link', { name: '任务', exact: true }).waitFor()
    check(await sidebar(member.page).getByRole('link', { name: '大模型客户端', exact: true }).count() === 0, 'member 侧栏不应有大模型客户端')
    const memberRead = await api(member.page, workspace, 'GET', '/api/v1/llm_bridge/bridges')
    check(memberRead.status === 403, `member 不能查看 MCP 管理器：${memberRead.status}`)
    step('admin 看到只读视图、接口写操作 403；member 侧栏没有入口、接口 403')

    // ---------------------------------------------------------------- 手机宽度
    const phone = await login(browser, ownerEmail, problems, 'owner（手机）', { width: 390, height: 844 })
    await phone.page.goto(`${base}/llm_bridge?view=manage`)
    await account(phone.page, WORKBUDDY).getByText('未登录').waitFor({ timeout: 60000 })
    await platformsOn(phone.page, ONE)
    check(await noHorizontalScroll(phone.page), '手机宽度下页面不应横向滚动')
    for (const name of ['设置', '开发环境', '会话']) {
      await tab(phone.page, name).click()
      await phone.page.getByRole('tabpanel', { name }).waitFor()
      check(await noHorizontalScroll(phone.page), `手机宽度下“${name}”不应横向滚动`)
    }
    step('手机宽度（390）：管理器卡片和四个标签都不横向滚动')

    // ---------------------------------------------------------------- 停用与删除
    acceptNext(page)
    await card(page, ONE).getByRole('button', { name: `停用${ONE}` }).click()
    await page.getByText(`已停用「${ONE}」。`).waitFor()
    await page.getByRole('note').filter({ hasText: '这个 MCP 管理器已停用' }).waitFor()
    if (two) {
      // 删掉 2 号（先把它控制的平台改回全部：这个容器别的验收还要用）：只允许用它的实例一个都用不了，不会变成“都能用”
      const restoreTwo = await api(page, workspace, 'PUT', `/api/v1/llm_bridge/bridges/${twoId}/settings`, { platforms: ALL_PLATFORMS })
      check(restoreTwo.status === 200, `没能把 2 号改回控制全部平台：${JSON.stringify(restoreTwo)}`)
      acceptNext(page)
      await card(page, TWO).getByRole('button', { name: `删除${TWO}` }).click()
      await page.getByText(`已删除「${TWO}」。`).waitFor()
      await waitGrant((g) => Array.isArray(g.bridge_ids) && g.bridge_ids.length === 0, '删除 2 号后，只允许用它的实例应一个都不能用')
    }
    // 删除之前把改过的设置改回去：这个容器别的验收还要用
    const restore = await api(page, workspace, 'PUT', `/api/v1/llm_bridge/bridges/${oneId}/settings`, { platforms: ALL_PLATFORMS, concurrency: 2, claude_subscription_ack: false })
    check(restore.status === 200, `没能把 1 号改回控制全部平台：${JSON.stringify(restore)}`)
    acceptNext(page)
    await card(page, ONE).getByRole('button', { name: `删除${ONE}` }).click()
    await page.getByText(`已删除「${ONE}」。`).waitFor()
    await page.getByText('还没有 MCP 管理器。').waitFor()
    step(`停用后有提示；${two ? '删除 2 号后只允许用它的实例一个都不能用；' : ''}删除后回到空状态`)

    check(problems.length === 0, `检查过程中出现错误：\n  ${problems.join('\n  ')}`)
    console.log('大模型客户端管理浏览器验收全部通过')
  } finally {
    // 成败都收尾：把控制的平台改回全部、删掉这次添加的管理器、关掉模块（别的验收脚本按“模块默认停用”检查）
    if (ownerPage) {
      try {
        const left = await api(ownerPage, workspace, 'GET', '/api/v1/llm_bridge/bridges')
        for (const bridge of left.body?.bridges ?? []) {
          await api(ownerPage, workspace, 'PUT', `/api/v1/llm_bridge/bridges/${bridge.id}/settings`, { platforms: ALL_PLATFORMS })
          await api(ownerPage, workspace, 'DELETE', `/api/v1/llm_bridge/bridges/${bridge.id}`)
        }
        await api(ownerPage, workspace, 'PATCH', '/api/v1/modules/llm_bridge', { enabled: false })
      } catch (error) { console.error(`收尾失败：${error.message}`) }
    }
    await browser.close()
  }
}

main().catch((error) => { console.error(`失败  ${error.message}`); process.exit(1) })
